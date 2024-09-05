from typing import List

from rank_llm.data import Request, Result
from rank_llm.rerank import PromptMode
from rank_llm.rerank.listwise import RankListwiseOSLLM
from rank_llm.rerank.listwise.reorder.reorder_policy import ReorderPolicy, SlidingWindowReorderPolicy


class ZephyrReranker:
    def __init__(
            self,
            model_path: str = "castorini/rank_zephyr_7b_v1_full",
            context_size: int = 4096,
            prompt_mode: PromptMode = PromptMode.RANK_GPT,
            num_few_shot_examples: int = 0,
            device: str = "cuda",
            num_gpus: int = 1,
            variable_passages: bool = True,
            window_size: int = 20,
            reorder_policy: ReorderPolicy = None,
            system_message: str = "You are RankLLM, an intelligent assistant that can rank passages based on their relevancy to the query",
    ) -> None:
        if reorder_policy is None:
            reorder_policy = SlidingWindowReorderPolicy()

        self._reranker = RankListwiseOSLLM(
            model=model_path,
            name=model_path,
            context_size=context_size,
            prompt_mode=prompt_mode,
            num_few_shot_examples=num_few_shot_examples,
            device=device,
            num_gpus=num_gpus,
            variable_passages=variable_passages,
            window_size=window_size,
            system_message=system_message,
            reorder_policy=reorder_policy
        )

    def rerank_batch(
            self,
            requests: List[Request],
            rank_start: int = 0,
            rank_end: int = 100,
            window_size: int = 20,
            step: int = 10,
            shuffle_candidates: bool = False,
            logging: bool = False,
    ) -> List[Result]:
        """
        Reranks a list of requests using the Zephyr model.

        Args:
            requests (List[Request]): The list of requests. Each request has a query and a candidates list.
            rank_start (int, optional): The starting rank for processing. Defaults to 0.
            rank_end (int, optional): The end rank for processing. Defaults to 100.
            window_size (int, optional): The size of each sliding window. Defaults to 20.
            step (int, optional): The step size for moving the window. Defaults to 10.
            shuffle_candidates (bool, optional): Whether to shuffle candidates before reranking. Defaults to False.
            logging (bool, optional): Enables logging of the reranking process. Defaults to False.

        Returns:
            List[Result]: A list containing the reranked results.

        Note:
            check 'reranker.rerank_batch' for implementation details of reranking process.
        """
        return self._reranker.rerank_batch(
            requests=requests,
            rank_start=rank_start,
            rank_end=rank_end,
            window_size=window_size,
            step=step,
            shuffle_candidates=shuffle_candidates,
            logging=logging,
        )

    def rerank(
            self,
            request: Request,
            rank_start: int = 0,
            rank_end: int = 100,
            window_size: int = 20,
            step: int = 10,
            shuffle_candidates: bool = False,
            logging: bool = False,
    ) -> Result:
        """
        Reranks a request using the Zephyr model.

        Args:
            request (Request): The reranking request which has a query and a candidates list.
            rank_start (int, optional): The starting rank for processing. Defaults to 0.
            rank_end (int, optional): The end rank for processing. Defaults to 100.
            window_size (int, optional): The size of each sliding window. Defaults to 20.
            step (int, optional): The step size for moving the window. Defaults to 10.
            shuffle_candidates (bool, optional): Whether to shuffle candidates before reranking. Defaults to False.
            logging (bool, optional): Enables logging of the reranking process. Defaults to False.

        Returns:
            Result: the rerank result which contains the reranked candidates.

        Note:
            check 'reranker.rerank' for implementation details of reranking process.
        """
        return self._reranker.rerank(
            request=request,
            rank_start=rank_start,
            rank_end=rank_end,
            window_size=window_size,
            step=step,
            shuffle_candidates=shuffle_candidates,
            logging=logging,
        )
