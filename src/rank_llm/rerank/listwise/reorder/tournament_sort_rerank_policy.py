from dataclasses import dataclass
from typing import Dict, List, Tuple, Union

from rank_llm.data import Result

from .reorder_policy import ModelFunction, ReorderPolicy


@dataclass
class ResortRequest:
    indices: List[int]
    result: List[int]


class TournamentSortNode:
    @staticmethod
    def build(
        inds: List[int], window_size: int, top_k: int
    ) -> Tuple[
        "TournamentSortNode",
        List["TournamentSortNode"],
        Dict[int, "TournamentSortNode"],
    ]:
        assert window_size % top_k == 0
        children_size = window_size // top_k

        cs: List["TournamentSortNode"] = [
            TournamentSortNode(top_k=top_k, index=x) for x in inds
        ]

        base_nodes = {idx: c for idx, c in zip(inds, cs)}
        all_cs: List["TournamentSortNode"] = []
        all_cs.extend(cs)

        while len(cs) > 1:
            nxt = []
            for c in range(0, len(cs), children_size):
                children = cs[c : min(len(cs), c + children_size)]
                if len(children) == 1:
                    nxt.append(children[0])
                else:
                    nxt.append(TournamentSortNode(top_k=top_k, children=children))
                    all_cs.append(nxt[-1])

            cs = nxt

        return cs[0], all_cs, base_nodes

    def __init__(
        self,
        top_k: int,
        *,
        children: Union[List["TournamentSortNode"]] = None,
        index: int = None,
    ):
        super().__init__()

        self.parent: "TournamentSortNode" = None

        self._top_k = top_k

        if children is not None:
            for child in children:
                child.parent = self

            self._n = len(children)
            self._children = children
            self._top: List[int] = None
            self._tmp: List[int] = None
        else:
            self._n = -1
            self._index = index
            self._top: List[int] = [index]
            self._tmp: List[int] = None

    def reset(self):
        if self._n == -1:
            return
        self._top = None

    def invalidate(self):
        if self._n != -1:
            return

        self._top = []

    def get_resort_param(self) -> Union[List[int], None]:
        if self._n == -1 or self._top is not None:
            return None
        self._tmp = [x for child in self._children for x in child.top()]
        return [ind for ind in self._tmp]

    def resort(self, perm: List[int]):
        assert self._tmp is not None and self._top is None

        tops = []
        for i in perm:
            if len(tops) > self._top_k:
                break
            ind = self._tmp[i]
            if ind not in tops:
                tops.append(ind)

        self._top = tops

        return

    def top(self) -> List[int]:
        assert self._top is not None
        return self._top[: min(len(self._top), self._top_k)]

    def __str__(self):
        if self._n == -1:
            return f"[{self._index}]"
        else:
            return f"({' '.join([str(x) for x in self._children])})"


class TournamentSorter:
    def _get_random_indices(
        self, expect_size: int, ind_choices: List[int]
    ) -> List[int]:
        choices = set(ind_choices)
        result = []
        for j in reversed(range(self._n_passage)):
            if len(result) + len(ind_choices) >= expect_size:
                break
            if j not in choices:
                result.append(j)

        for j in reversed(range(self._n_passage)):
            if len(result) + len(ind_choices) >= expect_size:
                break
            result.append(j)
        return result

    def _pad_size(self, inds: List[int]) -> List[int]:
        if len(inds) >= self._window_size:
            return inds
        else:
            fitters = self._get_random_indices(self._window_size, inds)
            return inds + fitters

    def _unpad_perm(self, inds: List[int], padded: List[int], perm: List[int]):
        return [x for x in perm if x < len(inds)]

    def __init__(self, indices: List[int], window_size: int, r: int):
        super().__init__()
        self._window_size = window_size
        self._r = r

        self._n_passage = len(indices)

        self._tr, self._all_node, self._idx_to_node = TournamentSortNode.build(
            list(range(self._n_passage)), window_size=window_size, top_k=r
        )

    def _pop(self, x: int) -> List[TournamentSortNode]:
        on: TournamentSortNode = self._idx_to_node[x]
        lst = []
        while on is not None:
            lst.append(on)
            on.invalidate()
            on.reset()
            on = on.parent
        return lst

    def perform(self, top_k: int):
        result = []

        # firstly, simple sort
        for nd in self._all_node:
            resort_param = nd.get_resort_param()
            if resort_param is not None:
                padded = self._pad_size(resort_param)
                request = ResortRequest(padded, [])
                yield request
                cleaned_result = self._unpad_perm(resort_param, padded, request.result)
                nd.resort(cleaned_result)

        while len(result) < top_k:
            tpv = self._tr.top()[0]
            result.append(tpv)
            nodes = self._pop(tpv)
            for node in nodes:
                resort_param = node.get_resort_param()
                if resort_param is not None:
                    padded = self._pad_size(resort_param)
                    request = ResortRequest(padded, [])
                    yield request
                    assert len(request.result) > 0
                    cleaned_result = self._unpad_perm(
                        resort_param, padded, request.result
                    )
                    node.resort(cleaned_result)

        return result


class TournamentSortReorderPolicy(ReorderPolicy):
    def __init__(self, top_k: int, window_size: int):
        super().__init__()
        self._top_k = top_k
        self._window_size = window_size

    def reorder(
        self,
        requests: List[Result],
        rank_start: int,
        rank_end: int,
        model: ModelFunction,
        **kwargs,
    ) -> list[Result]:
        pass

    @staticmethod
    def name() -> str:
        return "reorder_policy.tournament_sort"

    def max_selected_indices(self) -> int:
        return self._window_size
