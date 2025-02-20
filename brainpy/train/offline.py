# -*- coding: utf-8 -*-

from typing import Dict, Sequence, Union, Callable

import numpy as np
import tqdm.auto
from jax.experimental.host_callback import id_tap

import brainpy.math as bm
from brainpy.algorithms.offline import get, RidgeRegression, OfflineAlgorithm
from brainpy.base import Base
from brainpy.dyn.base import DynamicalSystem
from brainpy.errors import NoImplementationError
from brainpy.modes import TrainingMode
from brainpy.tools.checking import serialize_kwargs
from brainpy.types import Array, Output
from .base import DSTrainer

__all__ = [
  'OfflineTrainer',
  'RidgeTrainer',
]


class OfflineTrainer(DSTrainer):
  """Offline trainer for models with recurrent dynamics.

  Parameters
  ----------
  target: DynamicalSystem
    The target model to train.
  fit_method: OfflineAlgorithm, Callable, dict, str
    The fitting method applied to the target model.
    - It can be a string, which specify the shortcut name of the training algorithm.
      Like, ``fit_method='ridge'`` means using the Ridge regression method.
      All supported fitting methods can be obtained through
      :py:func:`brainpy.nn.runners.get_supported_offline_methods`
    - It can be a dict, whose "name" item specifies the name of the training algorithm,
      and the others parameters specify the initialization parameters of the algorithm.
      For example, ``fit_method={'name': 'ridge', 'beta': 1e-4}``.
    - It can be an instance of :py:class:`brainpy.nn.runners.OfflineAlgorithm`.
      For example, ``fit_meth=bp.nn.runners.RidgeRegression(beta=1e-5)``.
    - It can also be a callable function, which receives three arguments "targets", "x" and "y".
      For example, ``fit_method=lambda targets, x, y: numpy.linalg.lstsq(x, targets)[0]``.
  **kwargs
    The other general parameters for RNN running initialization.
  """

  def __init__(
      self,
      target: DynamicalSystem,
      fit_method: Union[OfflineAlgorithm, Callable, Dict, str] = None,
      **kwargs
  ):
    self.true_numpy_mon_after_run = kwargs.get('numpy_mon_after_run', True)
    kwargs['numpy_mon_after_run'] = False
    super(OfflineTrainer, self).__init__(target=target, **kwargs)

    # get all trainable nodes
    nodes = self.target.nodes(level=-1, include_self=True).subset(DynamicalSystem).unique()
    self.train_nodes = tuple([node for node in nodes.values() if isinstance(node.mode, TrainingMode)])
    if len(self.train_nodes) == 0:
        raise ValueError('Found no trainable nodes.')

    # training method
    if fit_method is None:
      fit_method = RidgeRegression(alpha=1e-7)
    elif isinstance(fit_method, str):
      fit_method = get(fit_method)()
    elif isinstance(fit_method, dict):
      name = fit_method.pop('name')
      fit_method = get(name)(**fit_method)
    if not callable(fit_method):
      raise ValueError(f'"train_method" must be an instance of callable function, '
                       f'but we got {type(fit_method)}.')
    self.fit_method = fit_method
    # check the required interface in the trainable nodes
    self._check_interface()

    # set the training method
    for node in self.train_nodes:
      node.offline_fit_by = fit_method

    # initialize the fitting method
    for node in self.train_nodes:
      node.offline_init()

    # update dynamical variables
    if isinstance(self.fit_method, Base):
      self.dyn_vars.update(self.fit_method.vars().unique())

    # training function
    self._f_train = dict()

  def __repr__(self):
    name = self.__class__.__name__
    prefix = ' ' * len(name)
    return (f'{name}(target={self.target}, \n\t'
            f'{prefix}fit_method={self.fit_method})')

  def predict(
      self,
      inputs: Union[Array, Sequence[Array], Dict[str, Array]],
      reset_state: bool = False,
      shared_args: Dict = None,
      eval_time: bool = False
  ) -> Output:
    """Prediction function.

    What's different from `predict()` function in :py:class:`~.DynamicalSystem` is that
    the `inputs_are_batching` is default `True`.

    Parameters
    ----------
    inputs: Array, sequence of Array, dict of Array
      The input values.
    reset_state: bool
      Reset the target state before running.
    shared_args: dict
      The shared arguments across nodes.
    eval_time: bool
      Whether we evaluate the running time or not?

    Returns
    -------
    output: Array, sequence of Array, dict of Array
      The running output.
    """
    outs = super(OfflineTrainer, self).predict(inputs=inputs,
                                               reset_state=reset_state,
                                               shared_args=shared_args,
                                               eval_time=eval_time)
    for node in self.train_nodes:
      node.fit_record.clear()
    return outs

  def fit(
      self,
      train_data: Sequence,
      reset_state: bool = False,
      shared_args: Dict = None,
  ) -> Output:
    """Fit the target model according to the given training and testing data.

    Parameters
    ----------
    train_data: sequence of data
      It should be a pair of `(X, Y)` train set.
      - ``X``: should be a tensor or a dict of tensors with the shape of
        `(num_sample, num_time, num_feature)`, where `num_sample` is
        the number of samples, `num_time` is the number of the time step,
        and `num_feature` is the number of features.
      - ``Y``: Target values. A tensor or a dict of tensors.
        - If the shape of each tensor is `(num_sample, num_feature)`,
          then we will only fit the model with the only last output.
        - If the shape of each tensor is `(num_sample, num_time, num_feature)`,
          then the fitting happens on the whole data series.
    reset_state: bool
      Whether reset the initial states of the target model.
    shared_args: dict
      The shared keyword arguments for the target models.
    """
    if shared_args is None: shared_args = dict()
    shared_args['fit'] = shared_args.get('fit', True)

    # checking training and testing data
    if not isinstance(train_data, (list, tuple)):
      raise ValueError(f"{self.__class__.__name__} only support "
                       f"training data with the format of (X, Y) pair, "
                       f"but we got a {type(train_data)}.")
    if len(train_data) != 2:
      raise ValueError(f"{self.__class__.__name__} only support "
                       f"training data with the format of (X, Y) pair, "
                       f"but we got a sequence with length {len(train_data)}")
    xs, ys = train_data

    # prediction, get all needed data
    outs = self.predict(inputs=xs, reset_state=reset_state, shared_args=shared_args)

    # get all input data
    xs, num_step, num_batch = self._check_xs(xs, move_axis=False)

    # check target data
    ys = self._check_ys(ys, num_batch=num_batch, num_step=num_step, move_axis=False)

    # init progress bar
    if self.progress_bar:
      self._pbar = tqdm.auto.tqdm(total=len(self.train_nodes))
      self._pbar.set_description(f"Train {len(self.train_nodes)} nodes: ", refresh=True)

    # training
    monitor_data = dict()
    for node in self.train_nodes:
      key = f'{node.name}-fit_record'
      monitor_data[key] = self.mon.get(key)
    self.f_train(shared_args)(monitor_data, ys)
    del monitor_data

    # close the progress bar
    if self.progress_bar:
      self._pbar.close()

    # final things
    for node in self.train_nodes:
      self.mon.pop(f'{node.name}-fit_record')
      node.fit_record.clear()  # clear fit records
    if self.true_numpy_mon_after_run:
      for key in self.mon.keys():
        self.mon[key] = np.asarray(self.mon[key])

    return outs

  def f_train(self, shared_args: Dict = None) -> Callable:
    """Get training function."""
    shared_kwargs_str = serialize_kwargs(shared_args)
    if shared_kwargs_str not in self._f_train:
      self._f_train[shared_kwargs_str] = self._make_fit_func(shared_args)
    return self._f_train[shared_kwargs_str]

  def _make_fit_func(self, shared_args):
    shared_args = dict() if shared_args is None else shared_args

    def train_func(monitor_data: Dict[str, Array], target_data: Dict[str, Array]):
      for node in self.train_nodes:
        fit_record = monitor_data[f'{node.name}-fit_record']
        targets = target_data[node.name]
        node.offline_fit(targets, fit_record)
        if self.progress_bar:
          id_tap(lambda *args: self._pbar.update(), ())

    if self.jit['fit']:
      dyn_vars = self.target.vars()
      dyn_vars.update(self.dyn_vars)
      train_func = bm.jit(train_func, dyn_vars=dyn_vars.unique())
    return train_func

  def build_monitors(self, return_without_idx, return_with_idx, shared_args: dict):
    if shared_args.get('fit', False):
      def func(tdi):
        res = {k: v.value for k, v in return_without_idx.items()}
        res.update({k: v[idx] for k, (v, idx) in return_with_idx.items()})
        res.update({k: f(tdi) for k, f in self.fun_monitors.items()})
        res.update({f'{node.name}-fit_record': node.fit_record for node in self.train_nodes})
        return res
    else:
      def func(tdi):
        res = {k: v.value for k, v in return_without_idx.items()}
        res.update({k: v[idx] for k, (v, idx) in return_with_idx.items()})
        res.update({k: f(tdi) for k, f in self.fun_monitors.items()})
        return res

    return func

  def _check_interface(self):
    for node in self.train_nodes:
      if hasattr(node.offline_fit, 'not_customized'):
        if node.offline_fit.not_customized:
          raise NoImplementationError(
            f'The node \n\n{node}\n\n'
            f'is set to be trainable with {self.__class__.__name__} method. '
            f'However, it does not implement the required training '
            f'interface "offline_fit()" function. '
          )
      if hasattr(node.offline_init, 'not_customized'):
        if node.offline_init.not_customized:
          raise NoImplementationError(
            f'The node \n\n{node}\n\n'
            f'is set to be trainable with {self.__class__.__name__} method. '
            f'However, it does not implement the required training '
            f'interface "offline_init()" function. '
          )


class RidgeTrainer(OfflineTrainer):
  """Trainer of ridge regression, also known as regression with Tikhonov regularization.

  Parameters
  ----------
  target: TrainingSystem, DynamicalSystem
    The target model.
  beta: float
    The regularization coefficient.
  **kwarg
    Other common parameters for :py:class:`brainpy.nn.RNNTrainer``.
  """

  def __init__(self, target, alpha=1e-7, **kwargs):
    super(RidgeTrainer, self).__init__(target=target,
                                       fit_method=dict(name='ridge', alpha=alpha),
                                       **kwargs)
