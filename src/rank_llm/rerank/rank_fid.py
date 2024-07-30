import torch

from typing import List, Union, Dict, Tuple, Optional

from networkx import prominent_group

from rank_llm.rerank.rankllm import PromptMode, RankLLM
from rank_llm.rerank.lit5.model import FiD, FiDCrossAttentionScore
from rank_llm.data import Result

from transformers import T5Tokenizer


class RankFiDDistill(RankLLM):

    def __init__(
            self,
            model: str,
            context_size: int = 300,
            prompt_mode: PromptMode = PromptMode.LiT5,  # Placeholder for actual mode
            num_few_shot_examples: int = 0,
            window_size: int = 20,
            precision: str = 'bfloat16',
            device: str = 'cuda',
            batch_size: int = -1
    ) -> None:
        """
         Creates instance of the RankFiDDistill class, a specialized version of RankLLM designed from Lit5-Distill.
        """
        super().__init__(model=model, context_size=context_size, prompt_mode=prompt_mode,
                         num_few_shot_examples=num_few_shot_examples)
        # TODO use adaptor for this guy
        self._precision = precision
        self._tokenizer = T5Tokenizer.from_pretrained(model)
        self._llm = FiD.from_pretrained(model).to(device).eval()

        self._window_size = window_size
        self._device = device

        # TODO consider make them non magic
        self._batch_size = batch_size

        self._stride = 10
        self._answer_maxlength = 100

        self._output_token_estimate = None

        self._post_init()

    def _run_llm_by_length_unified(self, batch_prompts: List[List[str]]) -> List[Tuple[str, int]]:
        if len(batch_prompts) == 0:
            return []

        self._llm.eval()

        batch_size = len(batch_prompts)
        n_passages = len(batch_prompts[0])

        # single batch, unsqueeze
        inputs = {
            k: v.reshape(batch_size, -1).to(self._device)
            for k, v
            in self._tokenizer([prompt for prompts in batch_prompts for prompt in prompts],
                               return_tensors='pt',
                               padding='max_length',
                               truncation=True,
                               max_length=self.max_tokens()).items()
        }

        with torch.no_grad():
            outputs = self._llm.generate(
                **inputs,
                max_length=self._answer_maxlength,
                do_sample=False,
                n_passages=n_passages
            )

        decoded_outputs = self._tokenizer.decode(outputs[0], skip_special_tokens=True)

        # all token size should be equal
        return [(decoded_output, outputs.shape[1]) for decoded_output in decoded_outputs]

    def run_llm_batched(
            self, prompts: List[List[Dict[str, str]]], current_window_size: int
    ) -> List[Tuple[str, int]]:
        assert self._batch_size > 0, f"Requires batch_size > 0 for batched run llm"
        if len(prompts) == 0:
            return []

        # unfortunately, we are not allowed to use VLLM on T5. However, we could unify the prompts by passage size
        #   (which is commonly the same) then rerank stuff having same passage sizes

        prompt_infos =  [list(map(lambda x: x['text'], prompt)) for prompt in prompts]

        results = []

        for i in range(0, len(prompt_infos), self._batch_size):
            result_batch = self._run_llm_by_length_unified(prompt_infos[i:min(i + self._batch_size, len(prompt_infos))])
            results.extend(result_batch)

        return results


    def create_prompt_batched(
            self, results: List[Result], rank_start: int, rank_end: int, batch_size: int
    ) -> List[Tuple[List[Dict[str, str]], int]]:
        return [self.create_prompt(result, rank_start, rank_end) for result in results]

    def run_llm(self, prompts: List[Dict[str, str]], **kwargs) -> Tuple[str, int]:
        """
        Run the target language model with a passed in prompt.
        """

        return self._run_llm_by_length_unified([list(map(lambda x: x['text'], prompts))])[0]

    def create_prompt(
            self, result: Result, rank_start: int, rank_end: int
    ) -> Tuple[List[Dict[str, str]], int]:
        """
        Create a prompt based on the result and given ranking range.
        """

        # For now, we concat the prompt, because it seems LiT5 is also concatting the stuff
        prompts = [
            {
                "text": self._gen_passage(result.query.text, i + 1 - rank_start,
                                          self.convert_doc_to_prompt_content(
                                              result.candidates[i].doc,
                                              self.max_tokens()
                                          ))
            }
            for i in range(rank_start, rank_end)
        ]

        return prompts, sum(self.get_num_tokens(prompt['text']) for prompt in prompts)

    def get_num_tokens(self, prompt: Union[str, List[Dict[str, str]]]) -> int:
        """
        Abstract method to calculate the number of tokens contained in the given prompt.
        """
        if isinstance(prompt, str):
            return len(self._tokenizer.encode(prompt))
        elif isinstance(prompt, list):
            return sum(len(self._tokenizer.encode(item['text'])) for item in prompt)
        else:
            raise ValueError("Prompt must be a string or a list of dictionaries with a 'text' key.")

    def cost_per_1k_token(self, input_token: bool) -> float:
        return 0

    def num_output_tokens(self, current_window_size: Optional[int] = None) -> int:
        if current_window_size is None:
            current_window_size = self._window_size
        if self._output_token_estimate is not None and self._window_size == current_window_size:
            return self._output_token_estimate
        else:
            output_token_estimate = (
                    len(self._tokenizer.encode(" > ".join([f"[{i + 1}]" for i in range(current_window_size)]))) - 1
            )
            if self._output_token_estimate is None and self._window_size == current_window_size:
                self._output_token_estimate = output_token_estimate

            return output_token_estimate

    def _post_init(self):
        self._to_precision(self._precision)

    def _tokenize(self, s: str):
        return self._tokenizer(s)

    def _to_precision(self, precision: str) -> None:
        """
        We don't support python12 for now, after python 12, the code should be changed into
        """
        if precision == 'float32':
            self._llm = self._llm.float()
        elif precision == 'bfloat16':
            self._llm = self._llm.bfloat16()
        elif precision == 'float16':
            self._llm = self._llm.float16()

    @staticmethod
    def _gen_passage(query: str, index: int, passage: str) -> str:
        return f"Search Query: {query} Passage: [{index}] {passage} Relevance Ranking: "


class RankFiDScore(RankLLM):
    def __init__(
            self,
            model: str,
            context_size: int = 300,
            prompt_mode: PromptMode = PromptMode.LiT5,  # Placeholder for actual mode
            num_few_shot_examples: int = 0,
            window_size: int = 20,
            device: str = 'cuda',
    ) -> None:
        """
         Creates instance of the RankFiDScore class, a specialized version of RankLLM designed from Lit5-Score.
        """
        super().__init__(model=model, context_size=context_size, prompt_mode=prompt_mode,
                         num_few_shot_examples=num_few_shot_examples)
        self._tokenizer = T5Tokenizer.from_pretrained(model)
        self._llm = FiDCrossAttentionScore.from_pretrained(model).to(device).eval()

        self._window_size = window_size
        self._device = device

        self._batch_size = 1
        self._stride = 10
        self._answer_maxlength = 100
        self._n_passes = 1

        self._post_init()

        self._output_token_estimate = None

    def _post_init(self) -> None:
        # set the overwrite forward cross attention
        self._llm.overwrite_forward_crossattention()

    def run_llm_batched(
            self, prompts: List[Union[str, List[Dict[str, str]]]]
    ) -> List[Tuple[str, int]]:
        assert False, "Not supported batch"
        return []

    def create_prompt_batched(
            self, results: List[Result], rank_start: int, rank_end: int, batch_size: int
    ) -> List[Tuple[Union[str, List[Dict[str, str]]], int]]:
        assert False, "Not supported batch"
        return []

    def run_llm(self, prompts: List[Dict[str, str]], **kwargs) -> Tuple[str, int]:
        # get arbitrary query (they should be the same)
        query = prompts[0]['query']

        inputs = {
            k: v.reshape(*v.shape[:-2], -1).unsqueeze(0).to(self._device) for k, v in self._tokenizer(
                [prompt['text'] for prompt in prompts],
                return_tensors='pt',
                padding='max_length',
                truncation=True,
                max_length=self.max_tokens()
            ).items()
        }

        passage_ids = inputs['input_ids']
        passage_mask = inputs['attention_mask']

        with torch.no_grad():
            self._llm.reset_score_storage()

            outputs = self._llm.generate(
                **inputs,
                max_length=self._answer_maxlength,
                do_sample=False,
                n_passages=len(prompts)
            )

        output_length = 0
        for j in range(outputs.shape[1]):
            if outputs[0, j] == FiDCrossAttentionScore.ANSWER_EOS_TOKEN:
                output_length = j
                break
        else:
            output_length = outputs.shape[1]

        query_mask_reader = self._tokenizer(
            query,
            max_length=self.max_tokens(),
            padding="longest",
            truncation=True,
            return_tensors="pt",
            add_special_tokens=False,
        )['attention_mask'].bool()

        with torch.no_grad():
            crossattention_scores = self._llm.get_crossattention_scores(len(prompts),
                                                                        ids=passage_ids.cuda(self._device),
                                                                        mask=passage_mask.bool().to(self._device),
                                                                        mask_query=query_mask_reader.to(self._device),
                                                                        output_sequence_lengths=[output_length])
            # only supports normswoquery for now
            crossattention_score: torch.Tensor = crossattention_scores['normswoquery']
            sorted, idxes = torch.sort(crossattention_score, dim=-1)

        return (" > ".join(map(lambda x: f"[{x + 1}]", idxes.detach().cpu()[0].tolist())),
                output_length + crossattention_score.shape[1])

    def create_prompt(self, result: Result, rank_start: int, rank_end: int) -> Tuple[List[Dict[str, str]], int]:
        """
        Create a prompt based on the result and given ranking range.
        """
        query = result.query.text
        results = []

        sum_token = 0

        for i in range(rank_start, rank_end):
            results.append({
                'query': query,
                'text': self._gen_passage(
                    query,
                    self.convert_doc_to_prompt_content(result.candidates[i].doc, self.max_tokens())
                )
            })
            sum_token += len(self._tokenizer.encode(results[-1]['text']))

        return results, sum_token

    def get_num_tokens(self, prompt: str) -> int:
        return len(self._tokenizer.encode(prompt))

    def cost_per_1k_token(self, input_token: bool) -> float:
        return 0.

    def num_output_tokens(self, current_window_size: Optional[int] = None) -> int:
        if current_window_size is None:
            current_window_size = self._window_size
        if self._output_token_estimate is not None and self._window_size == current_window_size:
            return self._output_token_estimate
        else:
            output_token_estimate = (
                    len(self._tokenizer.encode(" > ".join([f"[{i + 1}]" for i in range(current_window_size)]))) - 1
            )
            if self._output_token_estimate is None and self._window_size == current_window_size:
                self._output_token_estimate = output_token_estimate

            return output_token_estimate

    @staticmethod
    def _gen_passage(query: str, passage: str) -> str:
        return f"question: {query} context: {passage}"
