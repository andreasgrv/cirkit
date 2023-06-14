from typing import Any, List

import torch
from torch import Tensor
from torch.nn import functional as F

from cirkit.region_graph import RegionNode

from .exp_family_input_layer import ExpFamilyInputLayer
from .normal_input_layer import _shift_last_axis_to

# TODO: rework docstrings


@torch.no_grad()
def _one_hot(x: Tensor, k: int, dtype: torch.dtype = torch.float32) -> Tensor:
    """One hot encoding."""
    ind = torch.zeros(*x.shape, k, dtype=dtype, device=x.device)
    ind.scatter_(dim=-1, index=x.unsqueeze(-1), value=1)
    return ind


class CategoricalInputLayer(ExpFamilyInputLayer):
    """Implementation of Categorical distribution."""

    def __init__(self, nodes: List[RegionNode], num_var: int, num_dims: int, k: int):
        """Init class.

        Args:
            nodes (List[RegionNode]): Passed to super.
            num_var (int): Number of vars.
            num_dims (int): Number of dims.
            k (int): k for category.
        """
        super().__init__(nodes, num_var, num_dims, num_stats=num_dims * k)
        self.k = k

    def reparam_function(self, params: Tensor) -> Tensor:
        """Do reparam.

        Args:
            params (Tensor): The params.

        Returns:
            Tensor: Reparams.
        """
        return F.softmax(params, dim=-1)

    def sufficient_statistics(self, x: Tensor) -> Tensor:
        """Get sufficient statistics.

        Args:
            x (Tensor): The input.

        Returns:
            Tensor: The stats.
        """
        # TODO: do we put this assert in super()?
        assert len(x.shape) == 2 or len(x.shape) == 3, "Input must be 2 or 3 dimensional tensor."

        stats = _one_hot(x.long(), self.k)
        return stats.reshape(-1, self.num_dims * self.k) if len(x.shape) == 3 else stats

    def expectation_to_natural(self, phi: Tensor) -> Tensor:
        """Get expectation to natural.

        Args:
            phi (Tensor): The input.

        Returns:
            Tensor: The expectation.
        """
        # TODO: how to save the shape
        array_shape = self.params_shape[1:3]
        theta = torch.clamp(phi, 1e-12, 1)
        theta = theta.reshape(self.num_var, *array_shape, self.num_dims, self.k)
        theta /= theta.sum(dim=-1, keepdim=True)
        theta = theta.reshape(self.num_var, *array_shape, self.num_dims * self.k)
        theta = torch.log(theta)
        return theta

    def log_normalizer(self, theta: Tensor) -> Tensor:
        """Get normalizer.

        Args:
            theta (Tensor): The input.

        Returns:
            Tensor: The normalizer.
        """
        return torch.zeros(()).to(theta)

    def log_h(self, x: Tensor) -> Tensor:
        """Get log h.

        Args:
            x (Tensor): the input.

        Returns:
            Tensor: The output.
        """
        return torch.zeros(()).to(x)

    def _sample(  # type: ignore[misc]
        self, num_samples: int, params: Tensor, dtype: torch.dtype = torch.float32, **_: Any
    ) -> Tensor:
        with torch.no_grad():
            # TODO: save shape
            array_shape = self.params_shape[1:3]
            dist = params.reshape(self.num_var, *array_shape, self.num_dims, self.k)
            cum_sum = torch.cumsum(dist[..., :-1], dim=-1)  # TODO: why slice to -1?
            rand = torch.rand(num_samples, *cum_sum.shape[:-1], 1, device=cum_sum.device)
            samples = torch.sum(rand > cum_sum, dim=-1).to(dtype)
            return _shift_last_axis_to(samples, 2)

    # TODO: why pass in dtype instead of cast outside?
    def _argmax(  # type: ignore[misc]
        self, params: Tensor, dtype: torch.dtype = torch.float32, **_: Any
    ) -> Tensor:
        with torch.no_grad():
            # TODO: save shape
            array_shape = self.params_shape[1:3]
            dist = params.reshape(self.num_var, *array_shape, self.num_dims, self.k)
            mode = torch.argmax(dist, dim=-1).to(dtype)
            return _shift_last_axis_to(mode, 1)