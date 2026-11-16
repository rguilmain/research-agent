"""Synthetic wire fixtures, NOT captured traffic or proof of live model access."""

import json

import httpx


def responses_stream(
    *, calls=(), text="Answer", usage=True, response_id="resp_test", reasoning=False
):
    base = {
        "id": response_id,
        "object": "response",
        "created_at": 1,
        "model": "gpt-6-luna",
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "output": [],
    }
    events = []

    def emit(kind, **data):
        events.append({"type": kind, "sequence_number": len(events), **data})

    emit("response.created", response={**base, "status": "in_progress"})
    if reasoning:
        emit(
            "response.reasoning_summary_text.delta",
            item_id="reason_test",
            output_index=1,
            summary_index=0,
            delta="PRIVATE-REASONING",
        )
    output = []
    for index, (name, args) in enumerate(calls):
        item = {
            "id": f"fc_{index}",
            "type": "function_call",
            "call_id": f"{response_id}_{index}",
            "name": name,
            "arguments": "",
            "status": "in_progress",
        }
        emit("response.output_item.added", output_index=index, item=item)
        arguments = json.dumps(args)
        emit(
            "response.function_call_arguments.delta",
            output_index=index,
            item_id=item["id"],
            delta=arguments,
        )
        emit(
            "response.function_call_arguments.done",
            output_index=index,
            item_id=item["id"],
            arguments=arguments,
            name=name,
        )
        item = {**item, "arguments": arguments, "status": "completed"}
        emit("response.output_item.done", output_index=index, item=item)
        output.append(item)
    if not calls:
        item = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "status": "in_progress",
            "content": [],
        }
        emit("response.output_item.added", output_index=0, item=item)
        emit(
            "response.content_part.added",
            output_index=0,
            content_index=0,
            item_id=item["id"],
            part={"type": "output_text", "text": "", "annotations": []},
        )
        emit(
            "response.output_text.delta",
            output_index=0,
            content_index=0,
            item_id=item["id"],
            delta=text,
            logprobs=[],
        )
        emit(
            "response.output_text.done",
            output_index=0,
            content_index=0,
            item_id=item["id"],
            text=text,
            logprobs=[],
        )
        item = {
            **item,
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}],
        }
        emit("response.output_item.done", output_index=0, item=item)
        output.append(item)
    final = {**base, "status": "completed", "output": output}
    if usage:
        final["usage"] = {
            "input_tokens": 100,
            "output_tokens": 30,
            "total_tokens": 130,
            "input_tokens_details": {"cached_tokens": 40},
            "output_tokens_details": {"reasoning_tokens": 20},
        }
    emit("response.completed", response=final)
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)
