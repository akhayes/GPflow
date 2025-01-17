import itertools
from functools import reduce

import tensorflow as tf

from .. import kernels
from .. import mean_functions as mfn
from ..inducing_variables import InducingPoints
from ..probability_distributions import DiagonalGaussian, Gaussian, MarkovGaussian
from . import dispatch
from .expectations import expectation

NoneType = type(None)


@dispatch.expectation.register(Gaussian, kernels.Sum, NoneType, NoneType, NoneType)
def _E(p, kernel, _, __, ___, nghp=None):
    """
    Compute the expectation:
    <\Sum_i diag(Ki_{X, X})>_p(X)
        - \Sum_i Ki_{.,.} :: Sum kernel

    :return: N
    """
    exps = [expectation(p, k, nghp=nghp) for k in kernel.kernels]
    return reduce(tf.add, exps)


@dispatch.expectation.register(Gaussian, kernels.Sum, InducingPoints, NoneType, NoneType)
def _E(p, kernel, inducing_variable, _, __, nghp=None):
    """
    Compute the expectation:
    <\Sum_i Ki_{X, Z}>_p(X)
        - \Sum_i Ki_{.,.} :: Sum kernel

    :return: NxM
    """
    exps = [expectation(p, (k, inducing_variable), nghp=nghp) for k in kernel.kernels]
    return reduce(tf.add, exps)


@dispatch.expectation.register(Gaussian, (mfn.Linear, mfn.Identity, mfn.Constant), NoneType, kernels.Sum,
                               InducingPoints)
def _E(p, mean, _, kernel, inducing_variable, nghp=None):
    """
    Compute the expectation:
    expectation[n] = <m(x_n)^T (\Sum_i Ki_{x_n, Z})>_p(x_n)
        - \Sum_i Ki_{.,.} :: Sum kernel

    :return: NxQxM
    """
    exps = [expectation(p, mean, (k, inducing_variable), nghp=nghp) for k in kernel.kernels]
    return reduce(tf.add, exps)


@dispatch.expectation.register(MarkovGaussian, mfn.Identity, NoneType, kernels.Sum, InducingPoints)
def _E(p, mean, _, kernel, inducing_variable, nghp=None):
    """
    Compute the expectation:
    expectation[n] = <x_{n+1} (\Sum_i Ki_{x_n, Z})>_p(x_{n:n+1})
        - \Sum_i Ki_{.,.} :: Sum kernel

    :return: NxDxM
    """
    exps = [expectation(p, mean, (k, inducing_variable), nghp=nghp) for k in kernel.kernels]
    return reduce(tf.add, exps)


@dispatch.expectation.register((Gaussian, DiagonalGaussian), kernels.Sum, InducingPoints, kernels.Sum, InducingPoints)
def _E(p, kern1, feat1, kern2, feat2, nghp=None):
    """
    Compute the expectation:
    expectation[n] = <(\Sum_i K1_i_{Z1, x_n}) (\Sum_j K2_j_{x_n, Z2})>_p(x_n)
        - \Sum_i K1_i_{.,.}, \Sum_j K2_j_{.,.} :: Sum kernels

    :return: NxM1xM2
    """
    crossexps = []

    if kern1 == kern2 and feat1 == feat2:  # avoid duplicate computation by using transposes
        for i, k1 in enumerate(kern1.kernels):
            crossexps.append(expectation(p, (k1, feat1), (k1, feat1), nghp=nghp))

            for k2 in kern1.kernels[:i]:
                eKK = expectation(p, (k1, feat1), (k2, feat2), nghp=nghp)
                eKK += tf.linalg.adjoint(eKK)
                crossexps.append(eKK)
    else:
        for k1, k2 in itertools.product(kern1.kernels, kern2.kernels):
            crossexps.append(expectation(p, (k1, feat1), (k2, feat2), nghp=nghp))

    return reduce(tf.add, crossexps)
