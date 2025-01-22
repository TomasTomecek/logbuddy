import logging
import os
from typing import Iterator, List, Dict
from urllib.parse import urlparse
import numpy as np
import requests

from llama_cpp import Llama, CreateCompletionResponse, CreateCompletionStreamResponse
from logdetective.constants import PROMPT_TEMPLATE


LOG = logging.getLogger("logdetective")


def chunk_continues(text: str, index: int) -> bool:
    """Set of heuristics for determining whether or not
    does the current chunk of log text continue on next line.
    """
    conditionals = [
        lambda i, string: string[i + 1].isspace(),
        lambda i, string: string[i - 1] == "\\"
    ]

    for c in conditionals:
        y = c(index, text)
        if y:
            return True

    return False


def get_chunks(text: str):
    """Split log into chunks according to heuristic
    based on whitespace and backslash presence.
    """
    text_len = len(text)
    i = 0
    chunk = ""
    while i < text_len:
        chunk += text[i]
        if text[i] == '\n':
            if i + 1 < text_len and chunk_continues(text, i):
                i += 1
                continue
            yield chunk
            chunk = ""
        i += 1


def initialize_model(model_pth: str, filename_suffix: str = ".gguf", verbose: bool = False) -> Llama:
    """Initialize Llama class for inference.
    Args:
        model_pth (str): path to gguf model file or Hugging Face name
        filename_suffix (str): suffix of the model file name to be pulled from Hugging Face
        verbose (bool): level of verbosity for llamacpp
    """

    LOG.info("Loading model from %s", model_pth)

    if os.path.isfile(model_pth):
        model = Llama(
            model_path=model_pth,
            n_ctx=0,  # Maximum context for the model
            verbose=verbose,
            logits_all=True)
    else:
        model = Llama.from_pretrained(
            model_pth,
            f"*{filename_suffix}",
            n_ctx=0,  # Maximum context for the model
            verbose=verbose,
            logits_all=True)

    return model


def compute_certainty(probs: List[Dict]) -> float:
    """Compute certainty of repsponse based on average logit probability.
    Log probability is log(p), isn't really readable for most people, especially in compound.
    In this case it's just a matter of applying inverse operation exp.
    Of course that leaves you with a value in range <0, 1> so it needs to be multiplied by 100.
    Simply put, this is the most straightforward way to get the numbers out.

    This function is used in the server codebase.
    """

    top_logprobs = [
        np.exp(e["logprob"]) * 100 for e in probs]

    certainty = np.median(top_logprobs, axis=0)
    if np.isnan(certainty):
        raise ValueError("NaN certainty of answer")
    return certainty


def process_log(log: str, model: Llama, stream: bool) -> (
        CreateCompletionResponse | Iterator[CreateCompletionStreamResponse]):
    """Processes a given log using the provided language model and returns its summary.

    Args:
        log (str): The input log to be processed.
        model (Llama): The language model used for processing the log.

    Returns:
        str: The summary of the given log generated by the language model.
    """
    response = model(
        prompt=PROMPT_TEMPLATE.format(log),
        stream=stream,
        max_tokens=0,
        logprobs=1)

    return response


def retrieve_log_content(log_path: str) -> str:
    """Get content of the file on the log_path path.
    Path is assumed to be valid URL if it has a scheme.
    Otherwise it attempts to pull it from local filesystem."""
    parsed_url = urlparse(log_path)
    log = ""

    if not parsed_url.scheme:
        if not os.path.exists(log_path):
            raise ValueError(f"Local log {log_path} doesn't exist!")

        with open(log_path, "rt") as f:
            log = f.read()

    else:
        log = requests.get(log_path, timeout=60).text

    return log


def format_snippets(snippets: list[str]) -> str:
    """Format snippets, giving them separator, id and finally
    concatenating them.
    """
    summary = ""
    for i, s in enumerate(snippets):
        summary += f"""
        Snippet No. {i}:

        {s}
        ================
        """
    return summary


def validate_url(url: str) -> bool:
    """Validate incoming URL to be at least somewhat sensible for log files
    Only http and https protocols permitted. No result, params or query fields allowed.
    Either netloc or path must have non-zero length.
    """
    result = urlparse(url)
    if result.scheme not in ['http', 'https']:
        return False
    if any([result.params, result.query, result.fragment]):
        return False
    if not (result.path or result.netloc):
        return False
    return True
