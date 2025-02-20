# -*- coding: utf-8 -*-


import unittest

from brainpy.tools import checking


class TestUtils(unittest.TestCase):
  def test_check_shape(self):
    all_shapes = [
      (1, 2, 3),
      (1, 4),
      (10, 2, 4)
    ]
    free_shape, fixed_shapes = checking.check_shape(all_shapes, free_axes=-1)
    self.assertEqual(free_shape, [3, 4, 4])
    self.assertEqual(fixed_shapes, [10, 2])

  def test_check_shape2(self):
    all_shapes = [
      (1, 2, 3, 8,),
      (10, 1, 4, 10),
      (10, 2, 4, 100)
    ]
    free_shape, fixed_shapes = checking.check_shape(all_shapes, free_axes=[2, -1])
    print(free_shape)
    print(fixed_shapes)
    self.assertEqual(free_shape, [[3, 8], [4, 10], [4, 100]])
    self.assertEqual(fixed_shapes, [10, 2])

  def test_check_shape3(self):
    all_shapes = [
      (1, 2, 3, 8,),
      (10, 1, 4, 10),
      (10, 2, 4, 100)
    ]
    free_shape, fixed_shapes = checking.check_shape(all_shapes, free_axes=[0, 2, -1])
    print(free_shape)
    print(fixed_shapes)
    self.assertEqual(free_shape, [[1, 3, 8], [10, 4, 10], [10, 4, 100]])
    self.assertEqual(fixed_shapes, [2])

  def test_check_shape4(self):
    all_shapes = [
      (1, 2, 3, 8,),
      (10, 1, 4, 10),
      (10, 2, 4, 100)
    ]
    with self.assertRaises(ValueError):
      free_shape, fixed_shapes = checking.check_shape(all_shapes, free_axes=[0, -1])
