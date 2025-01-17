# Copyright 2017 Mark van der Wilk
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

import numpy as np
from numpy.testing import assert_allclose, assert_equal
import pytest
import gpflow
from gpflow.inducing_variables import InducingPoints, Multiscale, InducingPatches
from gpflow.covariances import Kuu, Kuf
from gpflow.config import default_jitter


@pytest.mark.parametrize('N, D', [[17, 3], [10, 7]])
def test_inducing_points_inducing_variable_len(N, D):
    Z = np.random.randn(N, D)
    inducing_variable = InducingPoints(Z)
    assert_equal(len(inducing_variable), N)


_kernel_setups = [
    gpflow.kernels.SquaredExponential(variance=0.46,
                                      lengthscale=np.random.uniform(0.5, 3., 5),
                                      ard=True),
    gpflow.kernels.Periodic(period=0.4, variance=1.8)
]


@pytest.mark.parametrize('N', [10, 101])
@pytest.mark.parametrize('kernel', _kernel_setups)
def test_inducing_equivalence(N, kernel):
    # Inducing inducing must be the same as the kernel evaluations
    Z = np.random.randn(N, 5)
    inducing_variable = InducingPoints(Z)
    assert_allclose(Kuu(inducing_variable, kernel), kernel(Z))


@pytest.mark.parametrize('N, M, D', [[23, 13, 3], [10, 5, 7]])
def test_multi_scale_inducing_equivalence_inducing_points(N, M, D):
    # Multiscale must be equivalent to inducing points when variance is zero
    Xnew, Z = np.random.randn(N, D), np.random.randn(M, D)
    rbf = gpflow.kernels.SquaredExponential(1.3441, lengthscale=np.random.uniform(0.5, 3., D))
    inducing_variable_zero_lengthscale = Multiscale(Z, scales=np.zeros(Z.shape))
    inducing_variable_inducing_point = InducingPoints(Z)

    multi_scale_Kuf = Kuf(inducing_variable_zero_lengthscale, rbf, Xnew)
    inducing_point_Kuf = Kuf(inducing_variable_inducing_point, rbf, Xnew)

    deviation_percent_Kuf = np.max(
        np.abs(multi_scale_Kuf - inducing_point_Kuf) / inducing_point_Kuf *
        100)
    assert deviation_percent_Kuf < 0.1

    multi_scale_Kuu = Kuu(inducing_variable_zero_lengthscale, rbf)
    inducing_point_Kuu = Kuu(inducing_variable_inducing_point, rbf)

    deviation_percent_Kuu = np.max(
        np.abs(multi_scale_Kuu - inducing_point_Kuu) / inducing_point_Kuu *
        100)
    assert deviation_percent_Kuu < 0.1


_inducing_variables_and_kernels = [
    [
        2,
        InducingPoints(np.random.randn(71, 2)),
        gpflow.kernels.SquaredExponential(variance=1.84,
                                          lengthscale=np.random.uniform(0.5, 3., 2))
    ],
    [
        2,
        InducingPoints(np.random.randn(71, 2)),
        gpflow.kernels.Matern12(variance=1.84,
                                lengthscale=np.random.uniform(0.5, 3., 2))
    ],
    [
        2,
        Multiscale(np.random.randn(71, 2),
                   np.random.uniform(0.5, 3, size=(71, 2))),
        gpflow.kernels.SquaredExponential(variance=1.84,
                                          lengthscale=np.random.uniform(0.5, 3., 2))
    ],
    [
        9,
        InducingPatches(np.random.randn(71, 4)),
        gpflow.kernels.Convolutional(gpflow.kernels.SquaredExponential(), [3, 3], [2, 2])
    ]
]


@pytest.mark.parametrize('input_dim, inducing_variable, kernel', _inducing_variables_and_kernels)
def test_inducing_variables_psd_schur(input_dim, inducing_variable, kernel):
    # Conditional variance must be PSD.
    X = np.random.randn(5, input_dim)
    Kuf_values = Kuf(inducing_variable, kernel, X)
    Kuu_values = Kuu(inducing_variable, kernel, jitter=default_jitter())
    Kff_values = kernel(X)
    Qff_values = Kuf_values.numpy().T @ np.linalg.solve(Kuu_values, Kuf_values)
    assert np.all(np.linalg.eig(Kff_values - Qff_values)[0] > 0.0)
