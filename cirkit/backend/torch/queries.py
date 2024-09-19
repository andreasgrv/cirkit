import functools
from abc import ABC

import torch
from torch import Tensor

from cirkit.backend.torch.circuits import TorchCircuit
from cirkit.backend.torch.layers import TorchInputLayer, TorchLayer
from cirkit.utils.scope import Scope


class Query(ABC):
    """An object used to run queries of circuits compiled using the torch backend."""

    def __init__(self) -> None:
        ...


class IntegrateQuery(Query):
    """The integration query object."""

    def __init__(self, circuit: TorchCircuit) -> None:
        """Initialize an integration query object.

        Args:
            circuit: The circuit to integrate over.
        """
        super().__init__()
        self._circuit = circuit

    def __call__(self, x: Tensor, *, vs: Scope) -> Tensor:
        """Solve an integration query, given an input batch and the variables to integrate.

        Args:
            x: An input batch of shape (B, C, D), where B is the batch size, C is the number of
                channels per variable, and D is the number of variables.
            vs: The variables to integrate. It must be a subset of the variables on which
                the circuit given in the constructor is defined on.

        Returns:
            The result of the integration query, given as a tensor of shape (B, O, K),
                where B is the batch size, O is the number of output vectors of the circuit, and
                K is the number of units in each output vector.
        """
        if not vs <= self._circuit.scope:
            raise ValueError("The variables to marginalize must be a subset of the circuit scope")
        vs_idx = torch.tensor(tuple(vs), device=self._circuit.device)
        output = self._circuit.evaluate(
            x, module_fn=functools.partial(IntegrateQuery._layer_fn, vs_idx=vs_idx)
        )  # (O, B, K)
        return output.transpose(0, 1)  # (B, O, K)

    @staticmethod
    def _layer_fn(layer: TorchLayer, x: Tensor, vs_idx: Tensor) -> Tensor:
        # Evaluate a layer: if it is not an input layer, then evaluate it in the usual
        # feed-forward way. Otherwise, use the variables to integrate to solve the marginal
        # queries on the input layers.
        output = layer(x)  # (F, B, Ko)
        if not isinstance(layer, TorchInputLayer):
            return output
        if layer.num_variables > 1:
            raise NotImplementedError("Integration of multivariate input layers is not supported")
        integration_mask = torch.isin(layer.scope_idx, vs_idx)  # Boolean mask of shape (F, 1)
        if not torch.any(integration_mask).item():
            return output
        # output: output of the layer of shape (F, B, Ko)
        # integration_mask: Boolean mask of shape (F, 1, 1)
        # integration_output: result of the integration of the layer of shape (F, 1, Ko)
        integration_mask = integration_mask.unsqueeze(dim=2)
        integration_output = layer.integrate()
        # Use the integration mask to select which output should be the result of
        # an integration operation, and which should not be
        # This is done in parallel for all folds, and regardless of whether the
        # circuit is folded or unfolded
        return torch.where(integration_mask, integration_output, output)
