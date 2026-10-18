import re

from piotr_voice_hub.ids import new_id


def test_ids_are_unique_and_carry_kind_prefix_and_counter() -> None:
    ids = [new_id("utterance") for _ in range(10_000)]
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"utterance-[0-9a-f]{12}-\d+", i) for i in ids)


def test_one_counter_for_all_kinds() -> None:
    a, b = new_id("listen"), new_id("cue")
    assert int(b.rsplit("-", 1)[1]) == int(a.rsplit("-", 1)[1]) + 1
    assert a.split("-")[1] == b.split("-")[1]  # same per-process prefix
