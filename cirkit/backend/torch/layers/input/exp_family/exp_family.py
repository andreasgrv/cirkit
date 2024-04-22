import functools
from abc import abstractmethod
from typing import Callable, Tuple

import torch
from torch import Tensor, nn

from cirkit.backend.torch.reparams import NormalizedReparam, Reparameterization
from cirkit.layers.input import InputLayer


class ExpFamilyLayer(InputLayer):
    """The abstract base class for Exponential Family distribution layers.

    Exponential Family dist:
        p(x|θ) = exp(η(θ) · T(x) + log_h(x) - A(η)).
    Ref: https://en.wikipedia.org/wiki/Exponential_family#Table_of_distributions.

    However here we directly parameterize η instead of θ:
        p(x|η) = exp(η · T(x) + log_h(x) - A(η)).
    Implemtations provide inverse mapping from η to θ.

    This implementation is fully factorized over the variables if used as multivariate, i.e., \
    equivalent to num_vars (arity) univariate EF distributions followed by a Hadamard product of \
    the same arity.
    """

    # NOTE: suff_stats_shape should not be part of the interface for users, but should be set by
    #       subclasses based on the implementation. We assume it is already set before entering
    #       ExpFamilyLayer.__init__.
    suff_stats_shape: Tuple[int, ...]
    """The shape for sufficient statistics, as dim S (or *S). The last dim is for normalization, \
    if relevant."""

    def __init__(
        self,
        *,
        num_input_units: int,
        num_output_units: int,
        arity: int = 1,
        reparam: Reparameterization,
    ) -> None:
        """Init class.

        Args:
            num_input_units (int): The number of input units, i.e. number of channels for variables.
            num_output_units (int): The number of output units.
            arity (int, optional): The arity of the layer, i.e., number of variables in the scope. \
                Defaults to 1.
            reparam (Reparameterization): The reparameterization for layer parameters.
        """
        assert all(
            s for s in self.suff_stats_shape
        ), "The number of sufficient statistics must be positive."

        super().__init__(
            num_input_units=num_input_units,
            num_output_units=num_output_units,
            arity=arity,
            reparam=reparam,
        )

        self.params = reparam
        self.materialize_params((arity, num_output_units, *self.suff_stats_shape), dim=-1)

    @property
    def _default_initializer_(self) -> Callable[[Tensor], Tensor]:
        """The default inplace initializer for the parameters of this layer.

        The EF natural params are initialized to N(0, 1).
        """
        return functools.partial(nn.init.normal_, mean=0, std=1)

    def forward(self, x: Tensor) -> Tensor:
        """Run forward pass.

        Args:
            x (Tensor): The input to this layer, shape (H, *B, Ki).

        Returns:
            Tensor: The output of this layer, shape (*B, Ko).
        """
        return self.comp_space.from_log(self.log_prob(x))

    def log_prob(self, x: Tensor) -> Tensor:
        """Calculate log-probability log(p(x)) from input x.

        Args:
            x (Tensor): The input x, shape (H, *B, Ki).

        Returns:
            Tensor: The log-prob log_p, shape (*B, Ko).
        """
        eta = self.params()  # shape (H, Ko, *S).
        suff_stats = self.sufficient_stats(x)  # shape (H, *B, *S).

        # We need to flatten because we cannot have two ... in einsum for suff_stats as (H, *B, *S).
        # shape (H, Ko, S), (H, *B, S) -> (H, *B, Ko).
        eta_suff = torch.einsum(
            "hks,h...s->h...k",
            eta.flatten(start_dim=-(eta.ndim - 2)),
            suff_stats.flatten(start_dim=-(eta.ndim - 2)),
        )

        log_h = self.log_base_measure(x).unsqueeze(dim=-1)  # shape (H, *B) -> (H, *B, 1).
        # shape (H, Ko) -> (*B, H, Ko) -> (H, *B, Ko).
        log_part = (
            self.log_partition(eta, eta_normed=isinstance(self.params, NormalizedReparam))
            .expand(*x.shape[1:-1], -1, -1)
            .movedim(-2, 0)
        )

        # shape (H, *B, Ko) + (H, *B, 1) + (H, *B, Ko) -> (H, *B, Ko) -> (*B, Ko).
        return torch.sum(eta_suff + log_h - log_part, dim=0)

    @abstractmethod
    def sufficient_stats(self, x: Tensor) -> Tensor:
        """Calculate sufficient statistics T from input x.

        Args:
            x (Tensor): The input x, shape (H, *B, Ki).

        Returns:
            Tensor: The sufficient statistics T, shape (H, *B, *S).
        """

    @abstractmethod
    def log_base_measure(self, x: Tensor) -> Tensor:
        """Calculate log base measure log_h from input x.

        Args:
            x (Tensor): The input x, shape (H, *B, Ki).

        Returns:
            Tensor: The natural parameters eta, shape (H, *B).
        """

    @abstractmethod
    def log_partition(self, eta: Tensor, *, eta_normed: bool = False) -> Tensor:
        """Calculate log partition function A from natural parameters eta.

        Args:
            eta (Tensor): The natural parameters eta, shape (H, Ko, *S).
            eta_normed (bool, optional): Whether eta is produced by a NormalizedReparam. If True, \
                implementations may save some computation. Defaults to False.

        Returns:
            Tensor: The log partition function A, shape (H, Ko).
        """