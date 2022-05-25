#!/usr/bin/env python3

import typing  # noqa F401
import warnings

import torch

from ..utils.memoize import cached
from .root_linear_operator import RootLinearOperator
from .triangular_linear_operator import TriangularLinearOperator, _TriangularLinearOperatorBase


class CholLinearOperator(RootLinearOperator):
    def __init__(self, chol: _TriangularLinearOperatorBase, upper: bool = False):
        if not isinstance(chol, _TriangularLinearOperatorBase):
            warnings.warn(
                "chol argument to CholLinearOperator should be a TriangularLinearOperator. "
                "Passing a dense tensor will cause errors in future versions.",
                DeprecationWarning,
            )
            if torch.all(torch.tril(chol) == chol):
                chol = TriangularLinearOperator(chol, upper=False)
            elif torch.all(torch.triu(chol) == chol):
                chol = TriangularLinearOperator(chol, upper=True)
            else:
                raise ValueError("chol must be either lower or upper triangular")
        super().__init__(chol)
        self.upper = upper

    @property
    def _chol_diag(self):
        return self.root._diagonal()

    @cached(name="cholesky")
    def _cholesky(self, upper=False):
        if upper == self.upper:
            return self.root
        else:
            return self.root._transpose_nonbatch()

    @cached
    def _diagonal(self):
        # TODO: Can we be smarter here?
        return (self.root.to_dense() ** 2).sum(-1)

    def _solve(self, rhs, preconditioner, num_tridiag=0):
        if num_tridiag:
            return super()._solve(rhs, preconditioner, num_tridiag=num_tridiag)
        return self.root._cholesky_solve(rhs, upper=self.upper)

    @cached
    def to_dense(self):
        root = self.root
        if self.upper:
            res = root._transpose_nonbatch() @ root
        else:
            res = root @ root._transpose_nonbatch()
        return res.to_dense()

    @cached
    def inverse(self):
        Linv = self.root.inverse()  # this could be slow in some cases w/ structured lazies
        return CholLinearOperator(TriangularLinearOperator(Linv, upper=not self.upper), upper=not self.upper)

    def inv_quad(self, tensor, reduce_inv_quad=True):
        if self.upper:
            R = self.root._transpose_nonbatch().solve(tensor)
        else:
            R = self.root.solve(tensor)
        inv_quad_term = (R**2).sum(dim=-2)
        if inv_quad_term.numel() and reduce_inv_quad:
            inv_quad_term = inv_quad_term.sum(-1)
        return inv_quad_term

    def inv_quad_logdet(self, inv_quad_rhs=None, logdet=False, reduce_inv_quad=True):
        if not self.is_square:
            raise RuntimeError(
                "inv_quad_logdet only operates on (batches of) square (positive semi-definite) LinearOperators. "
                "Got a {} of size {}.".format(self.__class__.__name__, self.size())
            )

        if inv_quad_rhs is not None:
            if self.dim() == 2 and inv_quad_rhs.dim() == 1:
                if self.shape[-1] != inv_quad_rhs.numel():
                    raise RuntimeError(
                        "LinearOperator (size={}) cannot be multiplied with right-hand-side Tensor (size={}).".format(
                            self.shape, inv_quad_rhs.shape
                        )
                    )
            elif self.dim() != inv_quad_rhs.dim():
                raise RuntimeError(
                    "LinearOperator (size={}) and right-hand-side Tensor (size={}) should have the same number "
                    "of dimensions.".format(self.shape, inv_quad_rhs.shape)
                )
            elif self.shape[-1] != inv_quad_rhs.shape[-2]:
                raise RuntimeError(
                    "LinearOperator (size={}) cannot be multiplied with right-hand-side Tensor (size={}).".format(
                        self.shape, inv_quad_rhs.shape
                    )
                )

        inv_quad_term = None
        logdet_term = None

        if inv_quad_rhs is not None:
            inv_quad_term = self.inv_quad(inv_quad_rhs, reduce_inv_quad=reduce_inv_quad)

        if logdet:
            logdet_term = self._chol_diag.pow(2).log().sum(-1)

        return inv_quad_term, logdet_term

    def root_inv_decomposition(self, method=None, initial_vectors=None, test_vectors=None):
        inv_root = self.root.inverse()
        return RootLinearOperator(inv_root._transpose_nonbatch())

    def solve(self, right_tensor, left_tensor=None):
        is_vector = right_tensor.ndim == 1
        if is_vector:
            right_tensor = right_tensor.unsqueeze(-1)
        res = self.root._cholesky_solve(right_tensor, upper=self.upper)
        if is_vector:
            res = res.squeeze(-1)
        if left_tensor is not None:
            res = left_tensor @ res
        return res