# -*- coding: utf-8 -*-

import sympy

from .diff_equation import DiffEquation
from .sympy_tools import str_to_sympy
from .sympy_tools import sympy_to_str
from .. import numpy as np
from .. import profile
from ..tools import word_replace

__all__ = [
    'get_integrator',
    'Integrator',
    'IntegratorError',
    'Euler',
    'Heun',
    'MidPoint',
    'RK2',
    'RK3',
    'RK4',
    'RK4Alternative',
    'ExponentialEuler',
    'MilsteinIto',
    'MilsteinStra',
]


def get_integrator(method):
    # If "profile._merge_integral" is True,
    #       we should return a "Integrator"
    #       instance, to help the "core_system" merge differential
    #       integration function into the main function.
    # else,
    #       we should return a "function", to help JIT user defined functions.

    method = method.lower()

    if method == 'euler':
        return Euler
    elif method == 'midpoint':
        return MidPoint
    elif method == 'heun':
        return Heun
    elif method == 'rk2':
        return RK2
    elif method == 'rk3':
        return RK3
    elif method == 'rk4':
        return RK4
    elif method == 'rk4_alternative':
        return RK4Alternative
    elif method == 'exponential':
        return ExponentialEuler
    elif method == 'milstein':
        return MilsteinIto
    elif method == 'milstein_ito':
        return MilsteinIto
    elif method == 'milstein_stra':
        return MilsteinStra
    else:
        raise ValueError(f'Unknown method: {method}.')


class IntegratorError(Exception):
    pass


class Integrator(object):
    def __init__(self, diff_eq):
        if not isinstance(diff_eq, DiffEquation):
            raise IntegratorError('"diff_eqs" must be an instance of DiffEquation.')
        self.diff_eq = diff_eq
        self._update_code = None
        self._update_func = None

    @staticmethod
    def get_nb_step(diff_eq, *args):
        raise NotImplementedError

    @staticmethod
    def get_np_step(diff_eq, *args):
        raise NotImplementedError

    def __call__(self, y0, t, *args):
        return self._update_func(y0, t, *args)

    @property
    def py_func_name(self):
        return self.diff_eq.func_name

    @property
    def update_code(self):
        return self._update_code

    @property
    def update_func(self):
        return self._update_func

    @property
    def code_scope(self):
        scope = self.diff_eq.func_scope
        scope['_normal_sample_'] = np.random._normal_sample_
        scope['np'] = np
        return scope


class Euler(Integrator):
    """Forward Euler method. Also named as ``explicit_Euler``.

    The simplest way for solving ordinary differential equations is "the
    Euler method" by Press et al. (1992) [1]_ :

    .. math::

        y_{n+1} = y_n + f(y_n, t_n) \\Delta t

    This formula advances a solution from :math:`y_n` to :math:`y_{n+1}=y_n+h`.
    Note that the method increments a solution through an interval :math:`h`
    while using derivative information from only the beginning of the interval.
    As a result, the step's error is :math:`O(h^2)`.

    For SDE equations, this approximation is a continuous time stochastic process that
    satisfy the iterative scheme [1]_.

    .. math::

        Y_{n+1} = Y_n + f(Y_n)h_n + g(Y_n)\\Delta W_n

    where :math:`n=0,1, \\cdots , N-1`, :math:`Y_0=x_0`, :math:`Y_n = Y(t_n)`,
    :math:`h_n = t_{n+1} - t_n` is the step size,
    :math:`\\Delta W_n = [W(t_{n+1}) - W(t_n)] \\sim N(0, h_n)=\\sqrt{h}N(0, 1)`
    with :math:`W(t_0) = 0`.

    For simplicity, we rewrite the above equation into

    .. math::

        Y_{n+1} = Y_n + f_n h + g_n \\Delta W_n

    As the order of convergence for the Euler-Maruyama method is low (strong order of
    convergence 0.5, weak order of convergence 1), the numerical results are inaccurate
    unless a small step size is used. By adding one more term from the stochastic
    Taylor expansion, one obtains a 1.0 strong order of convergence scheme known
    as *Milstein scheme* [2]_.

    Parameters
    ----------
    diff_eq : DiffEquation
        The differential equation.

    Returns
    -------
    func : callable
        The one-step numerical integrator function.

    References
    ----------
    .. [1] W. H.; Flannery, B. P.; Teukolsky, S. A.; and Vetterling,
            W. T. Numerical Recipes in FORTRAN: The Art of Scientific
            Computing, 2nd ed. Cambridge, England: Cambridge University
            Press, p. 710, 1992.
    .. [2] U. Picchini, Sde toolbox: Simulation and estimation of stochastic
           differential equations with matlab.
    """

    def __init__(self, diff_eq):
        super(Euler, self).__init__(diff_eq)
        if profile.is_numba_bk():
            self._update_code = self.get_nb_step(diff_eq)
        self._update_func = self.get_np_step(diff_eq)

    @staticmethod
    def get_nb_step(diff_eq, *args):
        dt = profile.get_dt()
        dt_sqrt = np.sqrt(dt)
        var_name = diff_eq.var_name
        func_name = diff_eq.func_name
        var = sympy.Symbol(var_name, real=True)

        # get code lines of df part
        f_expressions = diff_eq.get_f_expressions(substitute=False)
        code_lines = [str(expr) for expr in f_expressions]
        dfdt = sympy.Symbol(f'_df{var_name}_dt')

        # get code lines of dg part
        if diff_eq.is_stochastic:
            # code_lines.append(f'print(_normal_sample_(10))')
            # code_lines.append(f'print(_normal_sample_(np.array([1,2,3])))')
            code_lines.append(f'_{var_name}_dW = _normal_sample_({var_name})')
            # code_lines.append(f'_{var_name}_dW = np.random.normal(0., 1., {var_name}.shape)')
            if diff_eq.is_functional_noise:
                g_expressions = diff_eq.get_g_expressions(substitute=False)
                code_lines.extend([str(expr) for expr in g_expressions])
            else:
                code_lines.append(f'_dg{var_name}_dt = {diff_eq.g_code}')
            dgdt = sympy.Symbol(f'_{var_name}_dW') * sympy.Symbol(f'_dg{var_name}_dt')
        else:
            dgdt = 0

        # update expression
        update = var + dfdt * dt + dt_sqrt * dgdt
        code_lines.append(f'{var_name} = {sympy_to_str(update)}')

        # multiple returns
        return_expr = ', '.join([var_name] + diff_eq.f_returns[1:])
        code_lines.append(f'_{func_name}_res = {return_expr}')

        # final
        code = '\n'.join(code_lines)
        subs_dict = {arg: f'_{diff_eq.func_name}_{arg}' for arg in
                     diff_eq.func_args + diff_eq.f_expr_names + diff_eq.g_expr_names}
        code = word_replace(code, subs_dict)
        return code

    @staticmethod
    def get_np_step(diff_eqs, *args):
        __f = diff_eqs.f
        __dt = profile.get_dt()

        # SDE
        if diff_eqs.is_stochastic:
            __dt_sqrt = np.sqrt(__dt)
            __g = diff_eqs.g

            if callable(diff_eqs.g):

                if diff_eqs.is_multi_return:
                    def int_f(y0, t, *args):
                        dW = np.random.normal(0.0, 1.0, y0.shape)
                        val = __f(y0, t, *args)
                        df = val[0] * __dt
                        dg = __dt_sqrt * __g(y0, t, *args) * dW
                        y = y0 + df + dg
                        return (y,) + tuple(val[1:])

                else:
                    def int_f(y0, t, *args):
                        dW = np.random.normal(0.0, 1.0, y0.shape)
                        df = __f(y0, t, *args) * __dt
                        dg = __dt_sqrt * __g(y0, t, *args) * dW
                        return y0 + df + dg
            else:
                assert isinstance(diff_eqs.g, (int, float, np.ndarray))

                if diff_eqs.is_multi_return:
                    def int_f(y0, t, *args):
                        val = __f(y0, t, *args)
                        df = val[0] * __dt
                        dW = np.random.normal(0.0, 1.0, y0.shape)
                        dg = __dt_sqrt * __g * dW
                        y = y0 + df + dg
                        return (y,) + tuple(val[1:])
                else:
                    import numba
                    @numba.generated_jit(nopython=True)
                    def shape(x):
                        if isinstance(x, (numba.types.Float, numba.types.Integer)):
                            return lambda x: None
                        else:
                            return lambda x: x.shape

                    @numba.generated_jit(nopython=True)
                    def _normal_sample_(x):
                        if isinstance(x, numba.types.Float):
                            return lambda x: np.random.normal()
                        elif isinstance(x, numba.types.Integer):
                            return lambda x: np.random.normal()
                        else:
                            return lambda x: np.random.normal(0., 1., x.shape)

                    def int_f(y0, t, *args):
                        df = __f(y0, t, *args) * __dt
                        dW = _normal_sample_(y0)
                        dg = __dt_sqrt * __g * dW
                        return y0 + df + dg

        # ODE
        else:
            if diff_eqs.is_multi_return:
                def int_f(y0, t, *args):
                    val = __f(y0, t, *args)
                    y = y0 + __dt * val[0]
                    return (y,) + tuple(val[1:])

            else:
                def int_f(y0, t, *args):
                    return y0 + __dt * __f(y0, t, *args)

        return int_f


class RK2(Integrator):
    """Parametric second-order Runge-Kutta (RK2).
    Also named as ``RK2``.

    It is given in parametric form by [1]_ .

    .. math::

        k_1	&=	f(y_n, t_n)  \\\\
        k_2	&=	f(y_n + \\beta \\Delta t k_1, t_n + \\beta \\Delta t) \\\\
        y_{n+1} &= y_n + \\Delta t [(1-\\frac{1}{2\\beta})k_1+\\frac{1}{2\\beta}k_2]

    Parameters
    ----------
    diff_eq : DiffEquation
        The differential equation.
    beta : float
        Popular choices for 'beta':
        1/2 :
            explicit midpoint method
        2/3 :
            Ralston's method
        1 :
            Heun's method, also known as the explicit trapezoid rule

    Returns
    -------
    func : callable
        The one-step numerical integrator function.

    References
    ----------
    .. [1] https://lpsa.swarthmore.edu/NumInt/NumIntSecond.html

    See Also
    --------
    Heun, MidPoint
    """

    def __init__(self, diff_eq, beta=2 / 3):
        super(RK2, self).__init__(diff_eq)
        self.beta = beta
        if profile.is_numba_bk():
            self._update_code = self.get_nb_step(diff_eq, beta)
        self._update_func = self.get_np_step(diff_eq, __beta=beta)

    @staticmethod
    def get_nb_step(diff_eq, beta):
        dt = profile.get_dt()
        dt_sqrt = np.sqrt(dt)
        t_name = diff_eq.t_name
        var_name = diff_eq.var_name
        func_name = diff_eq.func_name
        var = sympy.Symbol(var_name, real=True)

        # get code lines of k1 df part
        k1_expressions = diff_eq.get_f_expressions(substitute=False)
        code_lines = [str(expr) for expr in k1_expressions[:-1]]
        code_lines.append(f'_df{var_name}_dt_k1 = {k1_expressions[-1].code}')

        # k1 -> k2 increment
        y_1_to_2 = f'_{func_name}_{var_name}_k1_to_k2'
        t_1_to_2 = f'_{func_name}_t_k1_to_k2'
        code_lines.append(f'{y_1_to_2} = {var_name} + {beta * dt} * _df{var_name}_dt_k1')
        code_lines.append(f'{t_1_to_2} = {t_name} + {beta * dt}')

        # get code lines of k2 df part
        k2_expressions = diff_eq.replace_f_expressions('k2', y_sub=y_1_to_2, t_sub=t_1_to_2)
        if len(k2_expressions):
            code_lines.extend([str(expr) for expr in k2_expressions[:-1]])
            code_lines.append(f'_df{var_name}_dt_k2 = {k2_expressions[-1].code}')

        # final dt part
        dfdt = sympy.Symbol(f'_df{var_name}_dt')
        if len(k2_expressions):
            coefficient2 = 1 / (2 * beta)
            coefficient1 = 1 - coefficient2
            code_lines.append(
                f'{dfdt.name} = {coefficient1} * _df{var_name}_dt_k1 + {coefficient2} * _df{var_name}_dt_k2')
        else:
            code_lines.append(f'{dfdt.name} = _df{var_name}_dt_k1')

        # get code lines of dg part
        if diff_eq.is_stochastic:
            raise NotImplementedError('RK2 currently doesn\'t support SDE.')
        else:
            dgdt = 0

        # update expression
        update = var + dfdt * dt + dt_sqrt * dgdt
        code_lines.append(f'{var_name} = {sympy_to_str(update)}')

        # multiple returns
        return_expr = ', '.join([var_name] + diff_eq.f_returns[1:])
        code_lines.append(f'_{func_name}_res = {return_expr}')

        # final
        code = '\n'.join(code_lines)
        subs_dict = {arg: f'_{diff_eq.func_name}_{arg}' for arg in
                     diff_eq.func_args + diff_eq.f_expr_names + diff_eq.g_expr_names}
        code = word_replace(code, subs_dict)
        return code

    @staticmethod
    def get_np_step(diff_eqs, __beta):
        __f = diff_eqs.f
        __dt = profile.get_dt()

        if diff_eqs.is_stochastic:
            raise NotImplementedError
        else:

            if diff_eqs.is_multi_return:
                def int_f(y0, t, *args):
                    val = __f(y0, t, *args)
                    k1 = val[0]
                    v = __f(y0 + __beta * __dt * k1, t + __beta * __dt, *args)
                    k2 = v[0]
                    y = y0 + __dt * ((1 - 1 / (2 * __beta)) * k1 + 1 / (2 * __beta) * k2)
                    return (y,) + tuple(val[1:])

            else:
                def int_f(y0, t, *args):
                    k1 = __f(y0, t, *args)
                    k2 = __f(y0 + __beta * __dt * k1, t + __beta * __dt, *args)
                    y = y0 + __dt * ((1 - 1 / (2 * __beta)) * k1 + 1 / (2 * __beta) * k2)
                    return y

        return int_f


class Heun(Integrator):
    """Two-stage method for numerical integrator.

    For ODE, please see "RK2".

    For stochastic Stratonovich integral, the Heun algorithm is given by,
    according to paper [1]_ [2]_.

    .. math::
        Y_{n+1} &= Y_n + f_n h + {1 \\over 2}[g_n + g(\\overline{Y}_n)] \\Delta W_n

        \\overline{Y}_n &= Y_n + g_n \\Delta W_n


    Or, it is written as [22]_

    .. math::

        Y_1 &= y_n + f(y_n)h + g_n \\Delta W_n

        y_{n+1} &= y_n + {1 \over 2}[f(y_n) + f(Y_1)]h + {1 \\over 2} [g(y_n) + g(Y_1)] \\Delta W_n

    Parameters
    ----------
    diff_eq : DiffEquation
        The differential equation.

    Returns
    -------
    func : callable
        The one-step numerical integrator function.

    References
    ----------
    .. [1] H. Gilsing and T. Shardlow, SDELab: A package for solving stochastic differential
         equations in MATLAB, Journal of Computational and Applied Mathematics 205 (2007),
         no. 2, 1002-1018.
    .. [2] P.reversal_potential. Kloeden, reversal_potential. Platen, and H. Schurz, Numerical solution of SDE through computer
         experiments, Springer, 1994.

    See Also
    --------
    RK2, MidPoint, MilsteinStra
    """

    def __init__(self, diff_eq):
        super(Heun, self).__init__(diff_eq)
        if profile.is_numba_bk():
            self._update_code = self.get_nb_step(diff_eq)
        self._update_func = self.get_np_step(diff_eq)

    @staticmethod
    def get_nb_step(diff_eq, *args):
        if diff_eq.is_stochastic:
            if callable(diff_eq.g):
                dt = profile.get_dt()
                dt_sqrt = np.sqrt(dt)
                var_name = diff_eq.var_name
                func_name = diff_eq.func_name
                var = sympy.Symbol(var_name, real=True)

                # k1 part #
                # ------- #

                # df
                f_k1_expressions = diff_eq.get_f_expressions(substitute=False)
                code_lines = [str(expr) for expr in f_k1_expressions[:-1]]
                code_lines.append(f'_df{var_name}_dt_k1 = {f_k1_expressions[-1].code}')

                # dg
                dW_sb = sympy.Symbol(f'_{var_name}_dW')
                code_lines.append(f'{dW_sb.name} = {dt_sqrt} * _normal_sample_({var_name})')
                assert diff_eq.is_functional_noise
                g_k1_expressions = diff_eq.get_g_expressions(substitute=False)
                code_lines.extend([str(expr) for expr in g_k1_expressions[:-1]])
                code_lines.append(f'_dg{var_name}_dt_k1 = {g_k1_expressions[-1].code}')

                # k1
                code_lines.append(f'_{func_name}_k1 = {var_name} + _df{var_name}_dt_k1 * {dt} + '
                                  f'_dg{var_name}_dt_k1 * {dW_sb.name}')

                # k2 part #
                # ------- #

                # df
                dfdt = sympy.Symbol(f'_df{var_name}_dt')
                f_k2_expressions = diff_eq.replace_f_expressions('k2', y_sub=f'_{func_name}_k1')
                if len(f_k2_expressions):
                    code_lines.extend([str(expr) for expr in f_k2_expressions[:-1]])
                    code_lines.append(f'_df{var_name}_dt_k2 = {f_k2_expressions[-1].code}')
                    code_lines.append(f'{dfdt.name} = (_df{var_name}_dt_k1 + _df{var_name}_dt_k2) / 2')
                else:
                    code_lines.append(f'{dfdt.name} = _df{var_name}_dt_k1')

                # dg
                dgdt = sympy.Symbol(f'_dg{var_name}_dt')
                g_k2_expressions = diff_eq.replace_f_expressions('k2', y_sub=f'_{func_name}_k1')
                if len(g_k2_expressions):
                    code_lines.extend([str(expr) for expr in g_k2_expressions[:-1]])
                    code_lines.append(f'_dg{var_name}_dt_k2 = {g_k2_expressions[-1].code}')
                    code_lines.append(f'{dgdt.name} = (_dg{var_name}_dt_k1 + _dg{var_name}_dt_k2) / 2')
                else:
                    code_lines.append(f'{dgdt.name} = _dg{var_name}_dt_k1')

                # update expression
                update = var + dfdt * dt + dgdt * dW_sb
                code_lines.append(f'{var_name} = {sympy_to_str(update)}')

                # multiple returns
                return_expr = ', '.join([var_name] + diff_eq.f_returns[1:])
                code_lines.append(f'_{func_name}_res = {return_expr}')

                # final
                code = '\n'.join(code_lines)
                subs_dict = {arg: f'_{diff_eq.func_name}_{arg}' for arg in
                             diff_eq.func_args + diff_eq.f_expr_names + diff_eq.g_expr_names}
                code = word_replace(code, subs_dict)
                return code
            else:
                return Euler.get_nb_step(diff_eq)
        else:
            return RK2.get_nb_step(diff_eq, 1.0)

    @staticmethod
    def get_np_step(diff_eqs, *args):
        __dt = profile.get_dt()
        __f = diff_eqs.f

        if diff_eqs.is_stochastic:
            __dt_sqrt = np.sqrt(__dt)
            __g = diff_eqs.g

            if callable(__g):
                if diff_eqs.is_multi_return:

                    def int_f(y0, t, *args):
                        dW = np.random.normal(0.0, 1.0, y0.shape)
                        val = __f(y0, t, *args)
                        df = val[0] * __dt
                        gn = __g(y0, t, *args)
                        y_bar = y0 + gn * dW * __dt_sqrt
                        gn_bar = __g(y_bar, t, *args)
                        dg = 0.5 * (gn + gn_bar) * dW * __dt_sqrt
                        y1 = y0 + df + dg
                        return (y1,) + tuple(val[1:])

                else:

                    def int_f(y0, t, *args):
                        dW = np.random.normal(0.0, 1.0, y0.shape)
                        df = __f(y0, t, *args) * __dt
                        gn = __g(y0, t, *args)
                        y_bar = y0 + gn * dW * __dt_sqrt
                        gn_bar = __g(y_bar, t, *args)
                        dg = 0.5 * (gn + gn_bar) * dW * __dt_sqrt
                        y1 = y0 + df + dg
                        return y1

                return int_f

            else:
                return Euler.get_np_step(diff_eqs)
        else:
            return RK2.get_np_step(diff_eqs, __beta=1.)


class MidPoint(Integrator):
    """Explicit midpoint Euler method. Also named as ``modified_Euler``.

    Parameters
    ----------
    diff_eq : DiffEquation
        The differential equation.

    Returns
    -------
    func : callable
        The one-step numerical integrator function.

    See Also
    --------
    RK2, Heun
    """

    def __init__(self, diff_eq):
        super(MidPoint, self).__init__(diff_eq)
        if profile.is_numba_bk():
            self._update_code = self.get_nb_step(diff_eq)
        self._update_func = self.get_np_step(diff_eq)

    @staticmethod
    def get_nb_step(diff_eq, *args):
        if diff_eq.is_stochastic:
            raise NotImplementedError
        else:
            return RK2.get_nb_step(diff_eq, 0.5)

    @staticmethod
    def get_np_step(diff_eqs, *args):
        return RK2.get_np_step(diff_eqs, __beta=0.5)


class RK3(Integrator):
    """Kutta's third-order method (commonly known as RK3).
    Also named as ``RK3`` [1]_ [2]_ [3]_ .

    .. math::

        k_1 &= f(y_n, t_n) \\\\
        k_2 &= f(y_n + \\frac{\\Delta t}{2}k_1, tn+\\frac{\\Delta t}{2}) \\\\
        k_3 &= f(y_n -\\Delta t k_1 + 2\\Delta t k_2, t_n + \\Delta t) \\\\
        y_{n+1} &= y_{n} + \\frac{\\Delta t}{6}(k_1 + 4k_2+k_3)

    Parameters
    ----------
    diff_eq : DiffEquation
        The differential equation.

    Returns
    -------
    func : callable
        The one-step numerical integrator function.

    References
    ----------
    .. [1] http://mathworld.wolfram.com/Runge-KuttaMethod.html
    .. [2] https://en.wikipedia.org/wiki/Runge%E2%80%93Kutta_methods
    .. [3] https://zh.wikipedia.org/wiki/龙格－库塔法

    """

    def __init__(self, diff_eq):
        super(RK3, self).__init__(diff_eq)
        if profile.is_numba_bk():
            self._update_code = self.get_nb_step(diff_eq)
        self._update_func = self.get_np_step(diff_eq)

    @staticmethod
    def get_nb_step(diff_eq, *args):
        dt = profile.get_dt()
        dt_sqrt = np.sqrt(dt)
        t_name = diff_eq.t_name
        var_name = diff_eq.var_name
        func_name = diff_eq.func_name
        var = sympy.Symbol(var_name, real=True)

        # get code lines of k1 df part
        k1_expressions = diff_eq.get_f_expressions(substitute=False)
        code_lines = [str(expr) for expr in k1_expressions[:-1]]
        code_lines.append(f'_df{var_name}_dt_k1 = {k1_expressions[-1].code}')

        # k1 -> k2 increment
        y_1_to_2 = f'_{func_name}_{var_name}_k1_to_k2'
        t_1_to_2 = f'_{func_name}_t_k1_to_k2'
        code_lines.append(f'{y_1_to_2} = {var_name} + {dt / 2} * _df{var_name}_dt_k1')
        code_lines.append(f'{t_1_to_2} = {t_name} + {dt / 2}')

        # get code lines of k2 df part
        k2_expressions = diff_eq.replace_f_expressions('k2', y_sub=y_1_to_2, t_sub=t_1_to_2)

        dfdt = sympy.Symbol(f'_df{var_name}_dt')
        if len(k2_expressions):
            code_lines.extend([str(expr) for expr in k2_expressions[:-1]])
            code_lines.append(f'_df{var_name}_dt_k2 = {k2_expressions[-1].code}')

            # get code lines of k3 df part
            y_1_to_3 = f'_{func_name}_{var_name}_k1_to_k3'
            t_1_to_3 = f'_{func_name}_t_k1_to_k3'
            code_lines.append(f'{y_1_to_3} = {var_name} - {dt} * _df{var_name}_dt_k1 + {2 * dt} * _df{var_name}_dt_k2')
            code_lines.append(f'{t_1_to_3} = {t_name} + {dt}')
            k3_expressions = diff_eq.replace_f_expressions('k3', y_sub=y_1_to_3, t_sub=t_1_to_3)
            code_lines.extend([str(expr) for expr in k3_expressions[:-1]])
            code_lines.append(f'_df{var_name}_dt_k3 = {k3_expressions[-1].code}')

            # final df part
            code_lines.append(f'{dfdt.name} = (_df{var_name}_dt_k1 + '
                              f'4 * _df{var_name}_dt_k2 + _df{var_name}_dt_k3) / 6')
        else:
            # final df part
            code_lines.append(f'{dfdt.name} = _df{var_name}_dt_k1')

        # get code lines of dg part
        if diff_eq.is_stochastic:
            raise NotImplementedError('RK3 currently doesn\'t support SDE.')
        else:
            dgdt = 0

        # update expression
        update = var + dfdt * dt + dt_sqrt * dgdt
        code_lines.append(f'{var_name} = {sympy_to_str(update)}')

        # multiple returns
        return_expr = ', '.join([var_name] + diff_eq.f_returns[1:])
        code_lines.append(f'_{func_name}_res = {return_expr}')

        # final
        code = '\n'.join(code_lines)
        subs_dict = {arg: f'_{diff_eq.func_name}_{arg}' for arg in
                     diff_eq.func_args + diff_eq.f_expr_names + diff_eq.g_expr_names}
        code = word_replace(code, subs_dict)
        return code

    @staticmethod
    def get_np_step(diff_eqs, *args):
        __f = diff_eqs.f
        __dt = profile.get_dt()

        if diff_eqs.is_stochastic:
            raise NotImplementedError

        else:
            if diff_eqs.is_multi_return:
                def int_f(y0, t, *args):
                    val = __f(y0, t, *args)
                    k1 = val[0]
                    k2 = __f(y0 + __dt / 2 * k1, t + __dt / 2, *args)[0]
                    k3 = __f(y0 - __dt * k1 + 2 * __dt * k2, t + __dt, *args)[0]
                    y = y0 + __dt / 6 * (k1 + 4 * k2 + k3)
                    return (y,) + tuple(val[1:])

            else:
                def int_f(y0, t, *args):
                    k1 = __f(y0, t, *args)
                    k2 = __f(y0 + __dt / 2 * k1, t + __dt / 2, *args)
                    k3 = __f(y0 - __dt * k1 + 2 * __dt * k2, t + __dt, *args)
                    return y0 + __dt / 6 * (k1 + 4 * k2 + k3)

        return int_f


class RK4(Integrator):
    """Fourth-order Runge-Kutta (RK4) [1]_ [2]_ [3]_ .

    .. math::

        k_1 &= f(y_n, t_n) \\\\
        k_2 &= f(y_n + \\frac{\\Delta t}{2}k_1, t_n + \\frac{\\Delta t}{2}) \\\\
        k_3 &= f(y_n + \\frac{\\Delta t}{2}k_2, t_n + \\frac{\\Delta t}{2}) \\\\
        k_4 &= f(y_n + \\Delta t k_3, t_n + \\Delta t) \\\\
        y_{n+1} &= y_n + \\frac{\\Delta t}{6}(k_1 + 2*k_2 + 2* k_3 + k_4)

    Parameters
    ----------
    diff_eq : DiffEquation
        The differential equation.

    Returns
    -------
    func : callable
        The one-step numerical integrator function.

    References
    ----------
    .. [1] http://mathworld.wolfram.com/Runge-KuttaMethod.html
    .. [2] https://en.wikipedia.org/wiki/Runge%E2%80%93Kutta_methods
    .. [3] https://zh.wikipedia.org/wiki/龙格－库塔法

    """

    def __init__(self, diff_eq):
        super(RK4, self).__init__(diff_eq)
        if profile.is_numba_bk():
            self._update_code = self.get_nb_step(diff_eq)
        self._update_func = self.get_np_step(diff_eq)

    @staticmethod
    def get_nb_step(diff_eq, *args):
        dt = profile.get_dt()
        dt_sqrt = np.sqrt(dt)
        t_name = diff_eq.t_name
        var_name = diff_eq.var_name
        func_name = diff_eq.func_name
        var = sympy.Symbol(var_name, real=True)

        # get code lines of k1 df part
        k1_expressions = diff_eq.get_f_expressions(substitute=False)
        code_lines = [str(expr) for expr in k1_expressions[:-1]]
        code_lines.append(f'_df{var_name}_dt_k1 = {k1_expressions[-1].code}')

        # k1 -> k2 increment
        y_1_to_2 = f'_{func_name}_{var_name}_k1_to_k2'
        t_1_to_2 = f'_{func_name}_t_k1_to_k2'
        code_lines.append(f'{y_1_to_2} = {var_name} + {dt / 2} * _df{var_name}_dt_k1')
        code_lines.append(f'{t_1_to_2} = {t_name} + {dt / 2}')

        # get code lines of k2 df part
        k2_expressions = diff_eq.replace_f_expressions('k2', y_sub=y_1_to_2, t_sub=t_1_to_2)

        dfdt = sympy.Symbol(f'_df{var_name}_dt')
        if len(k2_expressions):
            code_lines.extend([str(expr) for expr in k2_expressions[:-1]])
            code_lines.append(f'_df{var_name}_dt_k2 = {k2_expressions[-1].code}')

            # get code lines of k3 df part
            y_2_to_3 = f'_{func_name}_{var_name}_k2_to_k3'
            t_2_to_3 = f'_{func_name}_t_k2_to_k3'
            code_lines.append(f'{y_2_to_3} = {var_name} + {dt / 2} * _df{var_name}_dt_k2')
            code_lines.append(f'{t_2_to_3} = {t_name} + {dt / 2}')
            k3_expressions = diff_eq.replace_f_expressions('k3', y_sub=y_2_to_3, t_sub=t_2_to_3)
            code_lines.extend([str(expr) for expr in k3_expressions[:-1]])
            code_lines.append(f'_df{var_name}_dt_k3 = {k3_expressions[-1].code}')

            # get code lines of k4 df part
            y_3_to_4 = f'_{func_name}_{var_name}_k3_to_k4'
            t_3_to_4 = f'_{func_name}_t_k3_to_k4'
            code_lines.append(f'{y_3_to_4} = {var_name} + {dt} * _df{var_name}_dt_k3')
            code_lines.append(f'{t_3_to_4} = {t_name} + {dt}')
            k4_expressions = diff_eq.replace_f_expressions('k4', y_sub=y_3_to_4, t_sub=t_3_to_4)
            code_lines.extend([str(expr) for expr in k4_expressions[:-1]])
            code_lines.append(f'_df{var_name}_dt_k4 = {k4_expressions[-1].code}')

            # final df part
            code_lines.append(f'{dfdt.name} = (_df{var_name}_dt_k1 + 2 * _df{var_name}_dt_k2 + '
                              f'2 * _df{var_name}_dt_k3 + _df{var_name}_dt_k4) / 6')
        else:
            # final df part
            code_lines.append(f'{dfdt.name} = _df{var_name}_dt_k1')

        # get code lines of dg part
        if diff_eq.is_stochastic:
            raise NotImplementedError('RK4 currently doesn\'t support SDE.')
        else:
            dgdt = 0

        # update expression
        update = var + dfdt * dt + dt_sqrt * dgdt
        code_lines.append(f'{var_name} = {sympy_to_str(update)}')

        # multiple returns
        return_expr = ', '.join([var_name] + diff_eq.f_returns[1:])
        code_lines.append(f'_{func_name}_res = {return_expr}')

        # final
        code = '\n'.join(code_lines)
        subs_dict = {arg: f'_{diff_eq.func_name}_{arg}' for arg in
                     diff_eq.func_args + diff_eq.f_expr_names + diff_eq.g_expr_names}
        code = word_replace(code, subs_dict)
        return code

    @staticmethod
    def get_np_step(diff_eqs, *args):
        __f = diff_eqs.f
        __dt = profile.get_dt()

        if diff_eqs.is_stochastic:
            raise NotImplementedError

        else:
            if diff_eqs.is_multi_return:
                def int_f(y0, t, *args):
                    val = __f(y0, t, *args)
                    k1 = val[0]
                    k2 = __f(y0 + __dt / 2 * k1, t + __dt / 2, *args)[0]
                    k3 = __f(y0 + __dt / 2 * k2, t + __dt / 2, *args)[0]
                    k4 = __f(y0 + __dt * k3, t + __dt, *args)[0]
                    y = y0 + __dt / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
                    return (y,) + tuple(val[1:])

            else:
                def int_f(y0, t, *args):
                    k1 = __f(y0, t, *args)
                    k2 = __f(y0 + __dt / 2 * k1, t + __dt / 2, *args)
                    k3 = __f(y0 + __dt / 2 * k2, t + __dt / 2, *args)
                    k4 = __f(y0 + __dt * k3, t + __dt, *args)
                    return y0 + __dt / 6 * (k1 + 2 * k2 + 2 * k3 + k4)

        return int_f


class RK4Alternative(Integrator):
    """An alternative of fourth-order Runge-Kutta method.
    Also named as ``RK4_alternative`` ("3/8" rule).

    It is a less often used fourth-order
    explicit RK method, and was also proposed by Kutta [1]_:

    .. math::

        k_1 &= f(y_n, t_n) \\\\
        k_2 &= f(y_n + \\frac{\\Delta t}{3}k_1, t_n + \\frac{\\Delta t}{3}) \\\\
        k_3 &= f(y_n - \\frac{\\Delta t}{3}k_1 + \\Delta t k_2, t_n + \\frac{2 \\Delta t}{3}) \\\\
        k_4 &= f(y_n + \\Delta t k_1 - \\Delta t k_2 + \\Delta t k_3, t_n + \\Delta t) \\\\
        y_{n+1} &= y_n + \\frac{\\Delta t}{8}(k_1 + 3*k_2 + 3* k_3 + k_4)

    Parameters
    ----------
    diff_eq : DiffEquation
        The differential equation.

    Returns
    -------
    func : callable
        The one-step numerical integrator function.

    References
    ----------

    .. [1] https://en.wikipedia.org/wiki/List_of_Runge%E2%80%93Kutta_methods
    """

    def __init__(self, diff_eq):
        super(RK4Alternative, self).__init__(diff_eq)
        if profile.is_numba_bk():
            self._update_code = self.get_nb_step(diff_eq)
        self._update_func = self.get_np_step(diff_eq)

    @staticmethod
    def get_nb_step(diff_eq, *args):
        dt = profile.get_dt()
        dt_sqrt = np.sqrt(dt)
        t_name = diff_eq.t_name
        var_name = diff_eq.var_name
        func_name = diff_eq.func_name
        var = sympy.Symbol(var_name, real=True)

        # get code lines of k1 df part
        k1_expressions = diff_eq.get_f_expressions(substitute=False)
        code_lines = [str(expr) for expr in k1_expressions[:-1]]
        code_lines.append(f'_df{var_name}_dt_k1 = {k1_expressions[-1].code}')

        # k1 -> k2 increment
        y_1_to_2 = f'_{func_name}_{var_name}_k1_to_k2'
        t_1_to_2 = f'_{func_name}_t_k1_to_k2'
        code_lines.append(f'{y_1_to_2} = {var_name} + {dt / 3} * _df{var_name}_dt_k1')
        code_lines.append(f'{t_1_to_2} = {t_name} + {dt / 3}')

        # get code lines of k2 df part
        k2_expressions = diff_eq.replace_f_expressions('k2', y_sub=y_1_to_2, t_sub=t_1_to_2)

        dfdt = sympy.Symbol(f'_df{var_name}_dt')
        if len(k2_expressions):
            code_lines.extend([str(expr) for expr in k2_expressions[:-1]])
            code_lines.append(f'_df{var_name}_dt_k2 = {k2_expressions[-1].code}')

            # get code lines of k3 df part
            y_1_to_3 = f'_{func_name}_{var_name}_k1_to_k3'
            t_1_to_3 = f'_{func_name}_t_k1_to_k3'
            code_lines.append(f'{y_1_to_3} = {var_name} - {dt / 3} * _df{var_name}_dt_k1 + {dt} * _df{var_name}_dt_k2')
            code_lines.append(f'{t_1_to_3} = {t_name} + {dt * 2 / 3}')
            k3_expressions = diff_eq.replace_f_expressions('k3', y_sub=y_1_to_3, t_sub=t_1_to_3)
            code_lines.extend([str(expr) for expr in k3_expressions[:-1]])
            code_lines.append(f'_df{var_name}_dt_k3 = {k3_expressions[-1].code}')

            # get code lines of k4 df part
            y_1_to_4 = f'_{func_name}_{var_name}_k1_to_k4'
            t_1_to_4 = f'_{func_name}_t_k1_to_k4'
            code_lines.append(f'{y_1_to_4} = {var_name} + {dt} * _df{var_name}_dt_k1 - {dt} * _df{var_name}_dt_k2'
                              f'+ {dt} * _df{var_name}_dt_k3')
            code_lines.append(f'{t_1_to_4} = {t_name} + {dt}')
            k4_expressions = diff_eq.replace_f_expressions('k4', y_sub=y_1_to_4, t_sub=t_1_to_4)
            code_lines.extend([str(expr) for expr in k4_expressions[:-1]])
            code_lines.append(f'_df{var_name}_dt_k4 = {k4_expressions[-1].code}')

            # final df part
            code_lines.append(f'{dfdt.name} = (_df{var_name}_dt_k1 + 3 * _df{var_name}_dt_k2 + '
                              f'3 * _df{var_name}_dt_k3 + _df{var_name}_dt_k4) / 8')
        else:
            # final df part
            code_lines.append(f'{dfdt.name} = _df{var_name}_dt_k1')

        # get code lines of dg part
        if diff_eq.is_stochastic:
            raise NotImplementedError('RK4 currently doesn\'t support SDE.')
        else:
            dgdt = 0

        # update expression
        update = var + dfdt * dt + dt_sqrt * dgdt
        code_lines.append(f'{var_name} = {sympy_to_str(update)}')

        # multiple returns
        return_expr = ', '.join([var_name] + diff_eq.f_returns[1:])
        code_lines.append(f'_{func_name}_res = {return_expr}')

        # final
        code = '\n'.join(code_lines)
        subs_dict = {arg: f'_{diff_eq.func_name}_{arg}' for arg in
                     diff_eq.func_args + diff_eq.f_expr_names + diff_eq.g_expr_names}
        code = word_replace(code, subs_dict)
        return code

    @staticmethod
    def get_np_step(diff_eq, *args):
        assert isinstance(diff_eq, DiffEquation)

        __f = diff_eq.f
        __dt = profile.get_dt()

        if diff_eq.is_stochastic:
            raise IntegratorError('"RK4_alternative" method doesn\'t support stochastic differential equation.')

        else:

            if diff_eq.is_multi_return:
                def int_f(y0, t, *args):
                    val = __f(y0, t, *args)
                    k1 = val[0]
                    k2 = __f(y0 + __dt / 3 * k1, t + __dt / 3, *args)[0]
                    k3 = __f(y0 - __dt / 3 * k1 + __dt * k2, t + 2 * __dt / 3, *args)[0]
                    k4 = __f(y0 + __dt * k1 - __dt * k2 + __dt * k3, t + __dt, *args)[0]
                    y = y0 + __dt / 8 * (k1 + 3 * k2 + 3 * k3 + k4)
                    return (y,) + tuple(val[1:])

            else:
                def int_f(y0, t, *args):
                    k1 = __f(y0, t, *args)
                    k2 = __f(y0 + __dt / 3 * k1, t + __dt / 3, *args)
                    k3 = __f(y0 - __dt / 3 * k1 + __dt * k2, t + 2 * __dt / 3, *args)
                    k4 = __f(y0 + __dt * k1 - __dt * k2 + __dt * k3, t + __dt, *args)
                    return y0 + __dt / 8 * (k1 + 3 * k2 + 3 * k3 + k4)

        return int_f


class ExponentialEuler(Integrator):
    """First order, explicit exponential Euler method.

    For an ODE equation of the form

    .. math::

        y^{\\prime}=f(y), \quad y(0)=y_{0}

    its schema is given by

    .. math::

        y_{n+1}= y_{n}+h \\varphi(hA) f (y_{n})

    where :math:`A=f^{\prime}(y_{n})` and :math:`\\varphi(z)=\\frac{e^{z}-1}{z}`.

    For linear ODE system: :math:`y^{\\prime} = Ay + B`,
    the above equation is equal to

    .. math::

        y_{n+1}= y_{n}e^{hA}-B/A(1-e^{hA})

    For a SDE equation of the form

    .. math::

        d y=(Ay+ F(y))dt + g(y)dW(t) = f(y)dt + g(y)dW(t), \\quad y(0)=y_{0}

    its schema is given by [1]_

    .. math::

        y_{n+1} & =e^{\\Delta t A}(y_{n}+ g(y_n)\\Delta W_{n})+\\varphi(\\Delta t A) F(y_{n}) \\Delta t \\\\
         &= y_n + \\Delta t \\varphi(\\Delta t A) f(y) + e^{\\Delta t A}g(y_n)\\Delta W_{n}

    where :math:`\\varphi(z)=\\frac{e^{z}-1}{z}`.

    Parameters
    ----------
    diff_eq : DiffEquation
        The differential equation.

    Returns
    -------
    func : callable
        The one-step numerical integrator function.
    """

    def __init__(self, diff_eq):
        super(ExponentialEuler, self).__init__(diff_eq)
        if profile.is_numba_bk():
            self._update_code = self.get_nb_step(diff_eq)
        if not profile._merge_integral:
            raise IntegratorError('"exponential method only supports "_merge_integral=True". '
                                  'Please set "profile._merge_integral = True."')
        self._update_func = self.get_np_step(diff_eq)

    @staticmethod
    def get_nb_step(diff_eq, *args):
        dt = profile.get_dt()
        f_expressions = diff_eq.get_f_expressions(substitute=True)

        # code lines
        code_lines = [str(expr) for expr in f_expressions[:-1]]

        # get the linear system using sympy
        f_res = f_expressions[-1]
        df_expr = str_to_sympy(f_res.code).expand()
        s_df = sympy.Symbol(f"{f_res.var_name}")
        code_lines.append(f'{s_df.name} = {sympy_to_str(df_expr)}')
        var = sympy.Symbol(diff_eq.var_name, real=True)

        # get df part
        s_linear = sympy.Symbol(f'_{diff_eq.var_name}_linear')
        s_linear_exp = sympy.Symbol(f'_{diff_eq.var_name}_linear_exp')
        s_df_part = sympy.Symbol(f'_{diff_eq.var_name}_df_part')
        if df_expr.has(var):
            # linear
            linear = sympy.collect(df_expr, var, evaluate=False)[var]
            code_lines.append(f'{s_linear.name} = {sympy_to_str(linear)}')
            # linear exponential
            linear_exp = sympy.exp(linear * dt)
            code_lines.append(f'{s_linear_exp.name} = {sympy_to_str(linear_exp)}')
            # df part
            df_part = (s_linear_exp - 1) / s_linear * s_df
            code_lines.append(f'{s_df_part.name} = {sympy_to_str(df_part)}')

        else:
            # linear exponential
            code_lines.append(f'{s_linear_exp.name} = np.sqrt({dt})')
            # df part
            code_lines.append(f'{s_df_part.name} = {sympy_to_str(dt * s_df)}')

        # get dg part
        if diff_eq.is_stochastic:
            # dW
            code_lines.append(f'_{diff_eq.var_name}_dW = _normal_sample_({diff_eq.var_name})')
            # expressions of the stochastic part
            g_expressions = diff_eq.get_g_expressions()
            # get the
            if diff_eq.is_functional_noise:
                for expr in g_expressions:
                    code_lines.append(str(expr))
                g_expr = g_expressions[-1].var_name
            else:
                g_expr = g_expressions[-1].code
            # get the dg_part
            s_dg_part = sympy.Symbol(f'_{diff_eq.var_name}_dg_part')
            code_lines.append(f'_{diff_eq.var_name}_dg_part = {g_expr} * _{diff_eq.var_name}_dW')
        else:
            s_dg_part = 0

        # update expression
        update = var + s_df_part + s_dg_part * s_linear_exp

        # The actual update step
        code_lines.append(f'{diff_eq.var_name} = {sympy_to_str(update)}')
        return_expr = ', '.join([diff_eq.var_name] + diff_eq.f_returns[1:])
        code_lines.append(f'_{diff_eq.func_name}_res = {return_expr}')

        # final
        code = '\n'.join(code_lines)
        subs_dict = {arg: f'_{diff_eq.func_name}_{arg}' for arg in
                     diff_eq.func_args + diff_eq.f_expr_names + diff_eq.g_expr_names}
        code = word_replace(code, subs_dict)
        return code

    @staticmethod
    def get_np_step(diff_eq, *args):
        assert isinstance(diff_eq, DiffEquation)

        __f = diff_eq.f
        __dt = profile.get_dt()

        if diff_eq.is_stochastic:
            __dt_sqrt = np.sqrt(__dt)
            __g = diff_eq.g

            if callable(__g):

                if diff_eq.is_multi_return:

                    def int_f(y0, t, *args):
                        val = __f(y0, t, *args)
                        dydt, linear_part = val[0], val[1]
                        dW = np.random.normal(0.0, 1.0, y0.shape)
                        dg = __dt_sqrt * __g(y0, t, *args) * dW
                        exp = np.exp(linear_part * __dt)
                        y1 = y0 + (exp - 1) / linear_part * dydt + exp * dg
                        return (y1,) + tuple(val[2:])

                else:

                    def int_f(y0, t, *args):
                        dydt, linear_part = __f(y0, t, *args)
                        dW = np.random.normal(0.0, 1.0, y0.shape)
                        dg = __dt_sqrt * __g(y0, t, *args) * dW
                        exp = np.exp(linear_part * __dt)
                        y1 = y0 + (exp - 1) / linear_part * dydt + exp * dg
                        return y1

            else:
                assert isinstance(__g, (int, float, np.ndarray))

                if diff_eq.is_multi_return:

                    def int_f(y0, t, *args):
                        val = __f(y0, t, *args)
                        dydt, linear_part = val[0], val[1]
                        dW = np.random.normal(0.0, 1.0, y0.shape)
                        dg = __dt_sqrt * __g * dW
                        exp = np.exp(linear_part * __dt)
                        y1 = y0 + (exp - 1) / linear_part * dydt + exp * dg
                        return (y1,) + tuple(val[1:])

                else:

                    def int_f(y0, t, *args):
                        dydt, linear_part = __f(y0, t, *args)
                        dW = np.random.normal(0.0, 1.0, y0.shape)
                        dg = __dt_sqrt * __g * dW
                        exp = np.exp(linear_part * __dt)
                        y1 = y0 + (exp - 1) / linear_part * dydt + exp * dg
                        return y1

        else:

            if diff_eq.is_multi_return:

                def int_f(y0, t, *args):
                    val = __f(y0, t, *args)
                    df, linear_part = val[0], val[1]
                    y = y0 + (np.exp(linear_part * __dt) - 1) / linear_part * df
                    return (y,) + tuple(val[2:])

            else:

                def int_f(y0, t, *args):
                    df, linear_part = __f(y0, t, *args)
                    y = y0 + (np.exp(linear_part * __dt) - 1) / linear_part * df
                    return y

        return int_f


class MilsteinIto(Integrator):
    """Itô stochastic integral. The derivative-free Milstein method is
    an order 1.0 strong Taylor schema.

    The following implementation approximates this derivative thanks to a
    Runge-Kutta approach [1]_.

    In Itô scheme, it is expressed as

    .. math::

        Y_{n+1} = Y_n + f_n h + g_n \\Delta W_n + {1 \\over 2\\sqrt{h}}
        [g(\\overline{Y_n}) - g_n] [(\\Delta W_n)^2-h]

    where :math:`\\overline{Y_n} = Y_n + f_n h + g_n \\sqrt{h}`.

    Parameters
    ----------
    diff_eq : DiffEquation
        The differential equation.

    Returns
    -------
    func : callable
        The one-step numerical integrator function.

    References
    ----------
    .. [1] P.reversal_potential. Kloeden, reversal_potential. Platen, and H. Schurz, Numerical solution of SDE
           through computer experiments, Springer, 1994.

    """

    def __init__(self, diff_eq):
        super(MilsteinIto, self).__init__(diff_eq)
        if profile.is_numba_bk():
            self._update_code = self.get_nb_step(diff_eq)
        self._update_func = self.get_np_step(diff_eq)

    @staticmethod
    def get_nb_step(diff_eq, *args):
        if diff_eq.is_stochastic:
            g = diff_eq.g

            if callable(g):
                g_dependent_on_var = diff_eq.replace_f_expressions('test', y_sub=f'test')
                if len(g_dependent_on_var) == 0:
                    return Euler.get_nb_step(diff_eq)

                dt = profile.get_dt()
                dt_sqrt = np.sqrt(dt)
                var_name = diff_eq.var_name
                func_name = diff_eq.func_name
                var = sympy.Symbol(var_name, real=True)

                # k1 part #
                # ------- #

                # df
                f_k1_expressions = diff_eq.get_f_expressions(substitute=False)
                code_lines = [str(expr) for expr in f_k1_expressions]  # _df{var_name}_dt

                # dg
                dW_sb = sympy.Symbol(f'_{var_name}_dW')
                code_lines.append(f'{dW_sb.name} = {dt_sqrt} * _normal_sample_({var_name})')
                g_k1_expressions = diff_eq.get_g_expressions(substitute=False)
                code_lines.extend([str(expr) for expr in g_k1_expressions])  # _dg{var_name}_dt

                # k1
                code_lines.append(f'_{func_name}_k1 = {var_name} + _df{var_name}_dt * {dt} + '
                                  f'_dg{var_name}_dt * {dt_sqrt}')

                # high order part #
                # --------------- #

                # dg high order
                high_order = sympy.Symbol(f'_dg{var_name}_high_order')
                g_k2_expressions = diff_eq.replace_f_expressions('k2', y_sub=f'_{func_name}_k1')
                code_lines.extend([str(expr) for expr in g_k2_expressions[:-1]])
                code_lines.append(f'_dg{var_name}_dt_k2 = {g_k2_expressions[-1].code}')
                code_lines.append(f'{high_order.name} = {1 / 2. / dt_sqrt} * (_dg{var_name}_dt_k2 - _dg{var_name}_dt) *'
                                  f'({dW_sb.name} * {dW_sb.name} - {dt})')

                # update expression
                code_lines.append(f'{var_name} = {var_name} + _df{var_name}_dt * {dt} + '
                                  f'_dg{var_name}_dt * {dW_sb.name} + {high_order.name}')

                # multiple returns
                return_expr = ', '.join([var_name] + diff_eq.f_returns[1:])
                code_lines.append(f'_{func_name}_res = {return_expr}')

                # final
                code = '\n'.join(code_lines)
                subs_dict = {arg: f'_{diff_eq.func_name}_{arg}' for arg in
                             diff_eq.func_args + diff_eq.f_expr_names + diff_eq.g_expr_names}
                code = word_replace(code, subs_dict)
                return code

        return Euler.get_nb_step(diff_eq)

    @staticmethod
    def get_np_step(diff_eq, *args):
        assert isinstance(diff_eq, DiffEquation)

        __dt = profile.get_dt()
        __dt_sqrt = np.sqrt(__dt)
        __f = diff_eq.f
        __g = diff_eq.g

        if diff_eq.is_stochastic:
            if callable(__g):

                if diff_eq.is_multi_return:

                    def int_fg(y0, t, *args):
                        dW = np.random.normal(0.0, 1.0, y0.shape)
                        val = __f(y0, t, *args)
                        df = val[0] * __dt
                        g_n = __g(y0, t, *args)
                        dg = g_n * dW * __dt_sqrt
                        y_n_bar = y0 + df + g_n * __dt_sqrt
                        g_n_bar = __g(y_n_bar, t, *args)
                        y1 = y0 + df + dg + 0.5 * (g_n_bar - g_n) * (dW * dW * __dt_sqrt - __dt_sqrt)
                        return (y1,) + tuple(val[1:])

                else:

                    def int_fg(y0, t, *args):
                        dW = np.random.normal(0.0, 1.0, y0.shape)
                        df = __f(y0, t, *args) * __dt
                        g_n = __g(y0, t, *args)
                        dg = g_n * dW * __dt_sqrt
                        y_n_bar = y0 + df + g_n * __dt_sqrt
                        g_n_bar = __g(y_n_bar, t, *args)
                        y1 = y0 + df + dg + 0.5 * (g_n_bar - g_n) * (dW * dW * __dt_sqrt - __dt_sqrt)
                        return y1

                return int_fg

        return Euler.get_np_step(diff_eq)


class MilsteinStra(Integrator):
    """Heun two-stage stochastic numerical method for Stratonovich integral.

    Use the Stratonovich Heun algorithm to integrate Stratonovich equation,
    according to paper [1]_ [2]_.

    .. math::
        Y_{n+1} &= Y_n + f_n h + {1 \\over 2}[g_n + g(\\overline{Y}_n)] \\Delta W_n

        \\overline{Y}_n &= Y_n + g_n \\Delta W_n


    Or, it is written as [22]_

    .. math::

        Y_1 &= y_n + f(y_n)h + g_n \\Delta W_n

        y_{n+1} &= y_n + {1 \over 2}[f(y_n) + f(Y_1)]h + {1 \\over 2} [g(y_n) + g(Y_1)] \\Delta W_n


    Parameters
    ----------
    diff_eq : DiffEquation
        The differential equation.

    Returns
    -------
    func : callable
        The one-step numerical integrator function.

    References
    ----------

    .. [1] H. Gilsing and T. Shardlow, SDELab: A package for solving stochastic differential
         equations in MATLAB, Journal of Computational and Applied Mathematics 205 (2007),
         no. 2, 1002-1018.
    .. [2] P.reversal_potential. Kloeden, reversal_potential. Platen, and H. Schurz, Numerical solution of SDE through computer
         experiments, Springer, 1994.

    See Also
    --------
    MilsteinIto

    """

    def __init__(self, diff_eq):
        super(MilsteinStra, self).__init__(diff_eq)
        if profile.is_numba_bk():
            self._update_code = self.get_nb_step(diff_eq)
        self._update_func = self.get_np_step(diff_eq)

    @staticmethod
    def get_nb_step(diff_eq, *args):
        if diff_eq.is_stochastic:
            g = diff_eq.g

            if callable(g):
                g_dependent_on_var = diff_eq.replace_f_expressions('test', y_sub=f'test')
                if len(g_dependent_on_var) == 0:
                    return Euler.get_nb_step(diff_eq)

                dt = profile.get_dt()
                dt_sqrt = np.sqrt(dt)
                var_name = diff_eq.var_name
                func_name = diff_eq.func_name
                var = sympy.Symbol(var_name, real=True)

                # k1 part #
                # ------- #

                # df
                f_k1_expressions = diff_eq.get_f_expressions(substitute=False)
                code_lines = [str(expr) for expr in f_k1_expressions]  # _df{var_name}_dt

                # dg
                dW_sb = sympy.Symbol(f'_{var_name}_dW')
                code_lines.append(f'{dW_sb.name} = {dt_sqrt} * _normal_sample_({var_name})')
                g_k1_expressions = diff_eq.get_g_expressions(substitute=False)
                code_lines.extend([str(expr) for expr in g_k1_expressions])  # _dg{var_name}_dt

                # k1
                code_lines.append(f'_{func_name}_k1 = {var_name} + _df{var_name}_dt * {dt} + '
                                  f'_dg{var_name}_dt * {dt_sqrt}')

                # high order part #
                # --------------- #

                # dg high order
                high_order = sympy.Symbol(f'_dg{var_name}_high_order')
                g_k2_expressions = diff_eq.replace_f_expressions('k2', y_sub=f'_{func_name}_k1')
                code_lines.extend([str(expr) for expr in g_k2_expressions[:-1]])
                code_lines.append(f'_dg{var_name}_dt_k2 = {g_k2_expressions[-1].code}')
                code_lines.append(f'{high_order.name} = {1 / 2. / dt_sqrt} * (_dg{var_name}_dt_k2 - _dg{var_name}_dt) *'
                                  f'{dW_sb.name} * {dW_sb.name}')

                # update expression
                code_lines.append(f'{var_name} = {var_name} + _df{var_name}_dt * {dt} + '
                                  f'_dg{var_name}_dt * {dW_sb.name} + {high_order.name}')

                # multiple returns
                return_expr = ', '.join([var_name] + diff_eq.f_returns[1:])
                code_lines.append(f'_{func_name}_res = {return_expr}')

                # final
                code = '\n'.join(code_lines)
                subs_dict = {arg: f'_{diff_eq.func_name}_{arg}' for arg in
                             diff_eq.func_args + diff_eq.f_expr_names + diff_eq.g_expr_names}
                code = word_replace(code, subs_dict)
                return code

        return Euler.get_nb_step(diff_eq)

    @staticmethod
    def get_np_step(diff_eq, *args):

        assert isinstance(diff_eq, DiffEquation)

        __dt = profile.get_dt()
        __dt_sqrt = np.sqrt(__dt)
        __f = diff_eq.f

        if diff_eq.is_stochastic:
            __g = diff_eq.g

            if callable(__g):

                if diff_eq.is_multi_return:
                    def int_fg(y0, t, *args):
                        dW = np.random.normal(0.0, 1.0, y0.shape)
                        val = __f(y0, t, *args)
                        df = val[0] * __dt
                        g_n = __g(y0, t, *args)
                        dg = g_n * dW * __dt_sqrt
                        y_n_bar = y0 + df + g_n * __dt_sqrt
                        g_n_bar = __g(y_n_bar, t, *args)
                        extra_term = 0.5 * (g_n_bar - g_n) * (dW * dW * __dt_sqrt)
                        y1 = y0 + df + dg + extra_term
                        return (y1,) + tuple(val[1:])

                else:
                    def int_fg(y0, t, *args):
                        dW = np.random.normal(0.0, 1.0, y0.shape)
                        df = __f(y0, t, *args) * __dt
                        g_n = __g(y0, t, *args)
                        dg = g_n * dW * __dt_sqrt
                        y_n_bar = y0 + df + g_n * __dt_sqrt
                        g_n_bar = __g(y_n_bar, t, *args)
                        extra_term = 0.5 * (g_n_bar - g_n) * (dW * dW * __dt_sqrt)
                        y1 = y0 + df + dg + extra_term
                        return y1

                return int_fg

        return Euler.get_np_step(diff_eq)
