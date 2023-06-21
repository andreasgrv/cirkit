from abc import abstractmethod
from typing import Any, List

from torch import Tensor, nn

from cirkit.region_graph import PartitionNode

from ..layer import Layer

# TODO: relative import or absolute
# TODO: rework docstrings


class EinsumLayer(Layer):
    """Base for all einsums."""

    # TODO: kwargs should be public interface instead of `_`. How to supress this warning?
    #       all subclasses should accept all args as kwargs except for layer and k
    # TODO: subclasses should call reset_params -- where params are inited
    def __init__(  # type: ignore[misc]
        self,  # pylint: disable=unused-argument
        partition_layer: List[PartitionNode],
        k: int,
        **kwargs: Any,
    ) -> None:
        """Init class.

        Args:
            partition_layer (List[PartitionNode]): The current partition layer.
            k (int): The K.
            kwargs (Any): Passed to subclasses.
        """
        super().__init__()

        # TODO: do we really need these checks?
        # define in_k and out_k here, for subclass param init

        # TODO: check all constructions that can use comprehension
        out_k = set(
            out_region.k for partition in partition_layer for out_region in partition.outputs
        )
        assert (
            len(out_k) == 1
        ), f"The K of output region nodes in the same layer must be the same, got {out_k}."

        # check if it is root  # TODO: what does this mean?
        if out_k.pop() > 1:
            self.out_k = k
            # set num_sums in the graph  # TODO: but should decouple from RG
            for partition in partition_layer:
                for out_region in partition.outputs:
                    out_region.k = k
        else:
            self.out_k = 1

        # TODO: why do we check it here?
        assert all(
            len(partition.inputs) == 2 for partition in partition_layer
        ), "Only 2-partitions are currently supported."

        in_k = set(in_region.k for partition in partition_layer for in_region in partition.inputs)
        assert (
            len(in_k) == 1
        ), f"The K of output region nodes in the same layer must be the same, got {in_k}."
        self.in_k = in_k.pop()

    def reset_parameters(self) -> None:
        """Reset parameters to default initialization: U(0.01, 0.99)."""
        for param in self.parameters():
            nn.init.uniform_(param, 0.01, 0.99)

    # TODO: find a better way to do this override
    # TODO: what about abstract?
    @abstractmethod
    # pylint: disable=arguments-differ
    def forward(self, log_left: Tensor, log_right: Tensor) -> Tensor:  # type: ignore[override]
        """Compute the main Einsum operation of the layer.

        Do EinsumLayer forward pass.

        We assume that all parameters are in the correct range (no checks done).

        Skeleton for each EinsumLayer (options Xa and Xb are mutual exclusive \
            and follows an a-path o b-path)
        1) Go To exp-space (with maximum subtraction) -> NON SPECIFIC
        2a) Do the einsum operation and go to the log space || 2b) Do the einsum operation
        3a) do the sum                                      || 3b) do the product
        4a) go to exp space do the einsum and back to log   || 4b) do the einsum operation [OPT]
        5a) do nothing                                      || 5b) back to log space

        :param log_left: value in log space for left child.
        :param log_right: value in log space for right child.
        :return: result of the left operations, in log-space.
        """
