# Copyright 2016 James Hensman, Valentine Svensson, alexggmatthews, fujiisoup
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Optional

import numpy as np
import tensorflow as tf

import gpflow

from ..base import Parameter
from ..conditionals import conditional
from ..config import default_float, default_jitter
from ..kernels import Kernel
from ..kullback_leiblers import gauss_kl
from ..likelihoods import Likelihood
from ..mean_functions import MeanFunction, Zero
from ..models.model import Data, DataPoint, GPModel, MeanAndVariance
from ..utilities import triangular


class VGP(GPModel):
    r"""
    This method approximates the Gaussian process posterior using a multivariate Gaussian.

    The idea is that the posterior over the function-value vector F is
    approximated by a Gaussian, and the KL divergence is minimised between
    the approximation and the posterior.

    This implementation is equivalent to svgp with X=Z, but is more efficient.
    The whitened representation is used to aid optimization.

    The posterior approximation is

    .. math::

       q(\mathbf f) = N(\mathbf f \,|\, \boldsymbol \mu, \boldsymbol \Sigma)

    """
    def __init__(self,
                 data: Data,
                 kernel: Kernel,
                 likelihood: Likelihood,
                 mean_function: Optional[MeanFunction] = None,
                 num_latent: Optional[int] = None):
        """
        X is a data matrix, size [N, D]
        Y is a data matrix, size [N, R]
        kernel, likelihood, mean_function are appropriate GPflow objects

        """
        super().__init__(kernel, likelihood, mean_function, num_latent)

        x_data, y_data = data
        num_data = x_data.shape[0]
        self.num_data = num_data
        self.num_latent = num_latent or y_data.shape[1]
        self.data = data

        self.q_mu = Parameter(np.zeros((num_data, self.num_latent)))
        q_sqrt = np.array([np.eye(num_data) for _ in range(self.num_latent)])
        self.q_sqrt = Parameter(q_sqrt, transform=triangular())

    def log_likelihood(self):
        r"""
        This method computes the variational lower bound on the likelihood,
        which is:

            E_{q(F)} [ \log p(Y|F) ] - KL[ q(F) || p(F)]

        with

            q(\mathbf f) = N(\mathbf f \,|\, \boldsymbol \mu, \boldsymbol \Sigma)

        """

        x_data, y_data = self.data
        # Get prior KL.
        KL = gauss_kl(self.q_mu, self.q_sqrt)

        # Get conditionals
        K = self.kernel(x_data) + tf.eye(self.num_data, dtype=default_float()) * default_jitter()
        L = tf.linalg.cholesky(K)
        fmean = tf.linalg.matmul(L, self.q_mu) + self.mean_function(x_data)  # [NN, ND] -> ND
        q_sqrt_dnn = tf.linalg.band_part(self.q_sqrt, -1, 0)  # [D, N, N]
        L_tiled = tf.tile(tf.expand_dims(L, 0), tf.stack([self.num_latent, 1, 1]))
        LTA = tf.linalg.matmul(L_tiled, q_sqrt_dnn)  # [D, N, N]
        fvar = tf.reduce_sum(tf.square(LTA), 2)

        fvar = tf.transpose(fvar)

        # Get variational expectations.
        var_exp = self.likelihood.variational_expectations(fmean, fvar, y_data)

        return tf.reduce_sum(var_exp) - KL

    def predict_f(self, predict_at: DataPoint, full_cov: bool = False,
                  full_output_cov: bool = False) -> MeanAndVariance:
        x_data, _y_data = self.data
        mu, var = conditional(predict_at,
                              x_data,
                              self.kernel,
                              self.q_mu,
                              q_sqrt=self.q_sqrt,
                              full_cov=full_cov,
                              white=True)
        return mu + self.mean_function(predict_at), var


class VGPOpperArchambeau(GPModel):
    r"""
    This method approximates the Gaussian process posterior using a multivariate Gaussian.
    The key reference is:
    ::
      @article{Opper:2009,
          title = {The Variational Gaussian Approximation Revisited},
          author = {Opper, Manfred and Archambeau, Cedric},
          journal = {Neural Comput.},
          year = {2009},
          pages = {786--792},
      }
    The idea is that the posterior over the function-value vector F is
    approximated by a Gaussian, and the KL divergence is minimised between
    the approximation and the posterior. It turns out that the optimal
    posterior precision shares off-diagonal elements with the prior, so
    only the diagonal elements of the precision need be adjusted.
    The posterior approximation is
    .. math::
       q(\mathbf f) = N(\mathbf f \,|\, \mathbf K \boldsymbol \alpha,
                         [\mathbf K^{-1} + \textrm{diag}(\boldsymbol \lambda))^2]^{-1})

    This approach has only 2ND parameters, rather than the N + N^2 of vgp,
    but the optimization is non-convex and in practice may cause difficulty.

    """
    def __init__(self,
                 data: Data,
                 kernel: Kernel,
                 likelihood: Likelihood,
                 mean_function: MeanFunction = None,
                 num_latent: Optional[int] = None):
        """
        X is a data matrix, size [N, D]
        Y is a data matrix, size [N, R]
        kernel, likelihood, mean_function are appropriate GPflow objects
        """
        mean_function = Zero() if mean_function is None else mean_function

        super().__init__(kernel, likelihood, mean_function, num_latent)

        x_data, y_data = data
        self.data = data
        self.num_data = x_data.shape[0]
        self.num_latent = num_latent or y_data.shape[1]
        self.q_alpha = Parameter(np.zeros((self.num_data, self.num_latent)))
        self.q_lambda = Parameter(np.ones((self.num_data, self.num_latent)), transform=gpflow.utilities.positive())

    def log_likelihood(self):
        r"""
        q_alpha, q_lambda are variational parameters, size [N, R]
        This method computes the variational lower bound on the likelihood,
        which is:
            E_{q(F)} [ \log p(Y|F) ] - KL[ q(F) || p(F)]
        with
            q(f) = N(f | K alpha + mean, [K^-1 + diag(square(lambda))]^-1) .
        """
        x_data, y_data = self.data
        K = self.kernel(x_data)
        K_alpha = tf.linalg.matmul(K, self.q_alpha)
        f_mean = K_alpha + self.mean_function(x_data)

        # compute the variance for each of the outputs
        I = tf.tile(tf.eye(self.num_data, dtype=default_float())[None, ...], [self.num_latent, 1, 1])
        A = I + tf.transpose(self.q_lambda)[:, None, ...] * tf.transpose(self.q_lambda)[:, :, None, ...] * K
        L = tf.linalg.cholesky(A)
        Li = tf.linalg.triangular_solve(L, I)
        tmp = Li / tf.transpose(self.q_lambda)[:, None, ...]
        f_var = 1. / tf.square(self.q_lambda) - tf.transpose(tf.reduce_sum(tf.square(tmp), 1))

        # some statistics about A are used in the KL
        A_logdet = 2.0 * tf.reduce_sum(tf.math.log(tf.linalg.diag_part(L)))
        trAi = tf.reduce_sum(tf.square(Li))

        KL = 0.5 * (A_logdet + trAi - self.num_data * self.num_latent + tf.reduce_sum(K_alpha * self.q_alpha))

        v_exp = self.likelihood.variational_expectations(f_mean, f_var, y_data)
        return tf.reduce_sum(v_exp) - KL

    def predict_f(self, predict_at: DataPoint, full_cov: bool = False):
        r"""
        The posterior variance of F is given by
            q(f) = N(f | K alpha + mean, [K^-1 + diag(lambda**2)]^-1)
        Here we project this to F*, the values of the GP at Xnew which is given
        by
           q(F*) = N ( F* | K_{*F} alpha + mean, K_{**} - K_{*f}[K_{ff} +
                                           diag(lambda**-2)]^-1 K_{f*} )
        """

        x_data, _y_data = self.data
        # compute kernel things
        Kx = self.kernel(x_data, predict_at)
        K = self.kernel(x_data)

        # predictive mean
        f_mean = tf.linalg.matmul(Kx, self.q_alpha, transpose_a=True) + self.mean_function(predict_at)

        # predictive var
        A = K + tf.linalg.diag(tf.transpose(1. / tf.square(self.q_lambda)))
        L = tf.linalg.cholesky(A)
        Kx_tiled = tf.tile(Kx[None, ...], [self.num_latent, 1, 1])
        LiKx = tf.linalg.triangular_solve(L, Kx_tiled)
        if full_cov:
            f_var = self.kernel(predict_at) - tf.linalg.matmul(LiKx, LiKx, transpose_a=True)
        else:
            f_var = self.kernel(predict_at, full=False) - tf.reduce_sum(tf.square(LiKx), 1)
        return f_mean, tf.transpose(f_var)
