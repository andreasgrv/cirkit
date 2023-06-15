from abc import abstractmethod
from itertools import count
from typing import Dict, List, Optional, Tuple

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from cirkit.region_graph import PartitionNode, RegionNode

from ..layer import Layer

# TODO: relative import or absolute
# TODO: rework docstrings


class GenericEinsumLayer(Layer):  # pylint: disable=too-many-instance-attributes
    """Base for all einsums."""

    # TODO: is product a good name here? should split
    # TODO: input can be more generic than List
    # we have to provide operation for input, operation for product and operation after product
    def __init__(
        self,
        products: List[PartitionNode],
        layers: List[Layer],
        prod_exp: bool,
        k: int,
    ) -> None:
        """Init class.

        Args:
            products (List[PartitionNode]): The current product layer.
            layers (List[Layer]): All the layers currently.
            prod_exp (bool): I don't know.
            k (int): I don't know.
        """
        super().__init__()

        # self.r = r
        self.products = products
        # self.exp_reparam: bool = None
        self.prod_exp = prod_exp

        # assert decomposition_strategy in ["slice", "full"]
        # self.decomposition_strategy = decomposition_strategy

        # # # # # # # # #
        #   CHECK
        # # # # # # # # #

        # TODO: check all constructions that can use comprehension
        set_num_sums = set(n.num_dist for p in products for n in p.outputs)
        assert (
            len(set_num_sums) == 1
        ), "Number of distributions must be the same for all parent nodes in one layer."

        # check if it is root
        if (num_sums_from_graph := set_num_sums.pop()) > 1:
            self.num_sums = k
            # set num_sums in the graph
            # TODO: find another name for this
            successors = set(n for p in products for n in p.inputs)
            for n in successors:
                n.num_dist = k
        else:
            self.num_sums = num_sums_from_graph

        num_input_dist = set(n.num_dist for p in products for n in p.inputs)
        assert len(num_input_dist) == 1, (
            "Number of input distributions must be the same for all child nodes in one layer. "
            f"{num_input_dist}"
        )
        self.num_input_dist = num_input_dist.pop()

        assert all(
            len(p.inputs) == 2 for p in self.products
        ), "Only 2-partitions are currently supported."

        # # # # # # # # #
        #   BUILD
        # # # # # # # # #
        # TODO: should we do it here?
        # super(GenericEinsumLayer, self).__init__()

        self.params_mask = None  # TODO: check usage for this, and type?

        # get pairs of nodes which are input to the products (list of lists)
        # length of the outer list is same as self.products, length of inner lists is 2
        # "left child" has index 0, "right child" has index 1
        self.inputs = [sorted(p.inputs) for p in self.products]
        # TODO: again, why do we need sorting

        # collect all layers which contain left/right children
        self.left_layers = [
            l for l in layers if any(i[0].einet_address.layer == l for i in self.inputs)
        ]
        self.right_layers = [
            l for l in layers if any(i[1].einet_address.layer == l for i in self.inputs)
        ]

        # The following code does some index bookkeeping, in order that we can
        # gather the required data in forward(...).
        # Recall that in EiNets, each layer implements a log-density tensor of shape
        # (batch_size, vector_length, num_nodes).
        # We iterate over all previous left/right layers, and collect the node
        # indices (corresponding to axis 2) in
        # self.idx_layer_i_child_j, where i indexes into self.left_layers for
        # j==0, and into self.left_layers for j==1.
        # These index collections allow us to simply iterate over the previous
        # layers and extract the required node
        # slices in forward.
        # -------
        # Furthermore, the following code also generates self.permutation_child_0
        # and self.permutation_child_1,
        # which are permutations of the left and right input nodes. We need this
        # to get them in the same order as
        # assumed in self.products.
        # TODO: can we decouple this?
        def do_input_bookkeeping(layers: List[Layer], child_num: int) -> None:
            permutation: List[Optional[int]] = [None] * len(self.inputs)
            permutation_counter = count(0)
            for layer_counter, layer in enumerate(layers):
                cur_idx: List[int] = []
                for c, input_node in enumerate(self.inputs):
                    if input_node[child_num].einet_address.layer == layer:
                        cur_idx.append(input_node[child_num].einet_address.idx)
                        assert permutation[c] is None, "This should not happen."
                        permutation[c] = next(permutation_counter)
                # TODO: this way static checkers don't know what's registered
                self.register_buffer(
                    f"idx_layer_{layer_counter}_child_{child_num}", torch.tensor(cur_idx)
                )
            # TODO: if this should not happen, why have this? (or put in unit test?)
            assert all(i is not None for i in permutation), "This should not happen."
            self.register_buffer(f"permutation_child_{child_num}", torch.tensor(permutation))

        do_input_bookkeeping(self.left_layers, 0)
        do_input_bookkeeping(self.right_layers, 1)

        # when the EinsumLayer is followed by a EinsumMixingLayer, we produce a
        # dummy "node" which outputs 0 (-inf in log-domain) for zero-padding.
        self.dummy_idx: Optional[int] = None

        # the dictionary mixing_component_idx stores which nodes (axis 2 of the
        # log-density tensor) need to get mixed
        # in the following EinsumMixingLayer
        self.mixing_component_idx: Dict[RegionNode, List[int]] = {}

        for c, product in enumerate(self.products):
            # each product must have exactly 1 parent (sum node)
            nodes = product.outputs
            assert len(nodes) == 1
            node = nodes[0]

            if len(node.inputs) == 1:
                node.einet_address.layer = self
                node.einet_address.idx = c
            else:  # case followed by EinsumMixingLayer
                if node not in self.mixing_component_idx:
                    self.mixing_component_idx[node] = []
                self.mixing_component_idx[node].append(c)
                self.dummy_idx = len(self.products)

        # TODO: correct way to init? definitely not in _forward()
        self.left_child_log_prob = torch.empty(())
        self.right_child_log_prob = torch.empty(())

        self.reset_parameters()

    @property
    @abstractmethod
    def clamp_value(self) -> float:
        """Value for parameters clamping to keep all probabilities greater than 0.

        :return: value for parameters clamping
        """

    def clamp_params(self, clamp_all: bool = False) -> None:
        """Clamp parameters such that they are non-negative and \
        is impossible to get zero probabilities.

        This involves using a constant that is specific on the computation.

        Args:
            clamp_all (bool, optional): Whether to clamp all. Defaults to False.
        """
        for param in self.parameters():
            if clamp_all or param.requires_grad:
                # TODO: don't use .data but what about grad of nn.Param?
                param.data.clamp_(min=self.clamp_value)

    def reset_parameters(self) -> None:
        """Reset parameters to default initialization: U(0.01, 0.99)."""
        for param in self.parameters():
            nn.init.uniform_(param, 0.01, 0.99)

    @property
    def params_shape(self) -> List[Tuple[int, ...]]:
        """Return all param shapes.

        Returns:
            List[Tuple[int, ...]]: All shapes.
        """
        return [param.shape for param in self.parameters()]

    @property
    def num_params(self) -> int:
        """Return the total number of parameters of the layer.

        :return: the total number of parameters of the layer.
        """
        return sum(param.numel() for param in self.parameters())

    @abstractmethod
    def central_einsum(self, left_prob: torch.Tensor, right_prob: torch.Tensor) -> torch.Tensor:
        """Compute the main Einsum operation of the layer.

        :param left_prob: value in log space for left child.
        :param right_prob: value in log space for right child.
        :return: result of the left operations, in log-space.
        """

    # TODO: input not used? also no return?
    def forward(self, _: Optional[Tensor] = None) -> None:
        """Do EinsumLayer forward pass.

        We assume that all parameters are in the correct range (no checks done).

        Skeleton for each EinsumLayer (options Xa and Xb are mutual exclusive \
            and follows an a-path o b-path)
        1) Go To exp-space (with maximum subtraction) -> NON SPECIFIC
        2a) Do the einsum operation and go to the log space || 2b) Do the einsum operation
        3a) do the sum                                      || 3b) do the product
        4a) go to exp space do the einsum and back to log   || 4b) do the einsum operation [OPT]
        5a) do nothing                                      || 5b) back to log space
        """

        def _cidx(layer_counter: int, child_num: int) -> Tensor:
            # pylint: disable-next=unnecessary-dunder-call
            ret = self.__getattr__(f"idx_layer_{layer_counter}_child_{child_num}")
            # TODO: because getattr of Module can be Module
            # TODO: Tensor is Any. mypy or pytorch bug?
            assert isinstance(ret, Tensor)  # type: ignore[misc]
            return ret

        # iterate over all layers which contain "left" nodes, get their indices;
        # then, concatenate them to one tensor
        # TODO: we should use dim=2, check all code
        self.left_child_log_prob = torch.cat(
            [
                # TODO: why allow prob to be None?
                l.prob[:, :, _cidx(c, 0)]  # type: ignore[index,misc]
                for c, l in enumerate(self.left_layers)
            ],
            2,
        )
        # get into the same order as assumed in self.products
        # TODO: permutation_child_0 is in bookkeeping, can not normally find
        self.left_child_log_prob = self.left_child_log_prob[:, :, self.permutation_child_0]
        # ditto, for right "right" nodes
        self.right_child_log_prob = torch.cat(
            [
                l.prob[:, :, _cidx(c, 1)]  # type: ignore[index,misc]
                for c, l in enumerate(self.right_layers)
            ],
            2,
        )
        self.right_child_log_prob = self.right_child_log_prob[:, :, self.permutation_child_1]

        assert not torch.isinf(self.left_child_log_prob).any()
        assert not torch.isinf(self.right_child_log_prob).any()
        assert not torch.isnan(self.left_child_log_prob).any()
        assert not torch.isnan(self.right_child_log_prob).any()

        # # # # # # # # # # STEP 1: Go To the exp space # # # # # # # # # #
        # We perform the LogEinsumExp trick, by first subtracting the maxes
        log_prob = self.central_einsum(self.left_child_log_prob, self.right_child_log_prob)

        assert not torch.isinf(log_prob).any(), "Inf log prob"
        assert not torch.isnan(log_prob).any(), "NaN log prob"

        # zero-padding (-inf in log-domain) for the following mixing layer
        if self.dummy_idx:
            log_prob = F.pad(log_prob, [0, 1], "constant", float("-inf"))

        self.prob = log_prob
