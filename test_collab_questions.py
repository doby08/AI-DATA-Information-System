"""Temporary validation test for the Gemini+Ollama question-count collaboration."""
import sys
import ai_service

results = []


def check(name, condition, detail=""):
    results.append(("PASS" if condition else "FAIL", name, detail))


def patch(counts):
    """counts: list of ints — how many questions each fake AI call returns."""
    state = {"i": 0}

    def fake_gemini(system_type, user_role, max_questions, gemini_cfg,
                    extra_context=None, avoid_texts=None):
        n = counts[min(state["i"], len(counts) - 1)]
        state["i"] += 1
        return [{"text": f"Fake Gemini question {k} {' '.join(avoid_texts or [])}", "category": "goals"}
                for k in range(n)]

    def fake_ollama(system_type, user_role, max_questions, oll_cfg,
                    extra_context=None, avoid_texts=None):
        n = counts[min(state["i"], len(counts) - 1)]
        state["i"] += 1
        return [{"text": f"Fake Ollama question {k} {' '.join(avoid_texts or [])}", "category": "goals"}
                for k in range(n)]

    return fake_gemini, fake_ollama


def run_case(name, gemini_counts, ollama_counts, target, enable_gem, enable_oll):
    ai_service._gemini_questions, ai_service._ollama_questions = patch(gemini_counts + ollama_counts)
    cfg = {"enabled": enable_gem, "api_key": "fake", "model": "x",
           "ollama": {"enabled": enable_oll, "url": "http://x", "model": "x"}}
    qs = ai_service.generate_interview_questions("information_system", "Student",
                                                 target, cfg, extra_context="Test")
    unique = len({q["text"] for q in qs})
    check(name, len(qs) == target and unique == target,
          f"got {len(qs)} questions ({unique} unique), target {target}")


# 1. Gemini alone delivers the full count
run_case("Gemini alone: 10/10", [10], [], 10, True, False)
# 2. Gemini short (2/10) -> Ollama tops up (>=8)
run_case("Gemini 2 + Ollama tops up to 10", [2, 9], [], 10, True, True)
# 3. Ollama-only short (1/10) -> Gemini tops up
run_case("Ollama 1 + Gemini tops up to 10", [9, 1], [], 10, True, True)
# 4. Both short -> built-in pool pads to exact count
run_case("Both short -> padded to exact 10", [2, 3], [], 10, True, True)
# 5. Large count 50 with both AIs short -> still exactly 50
run_case("Both short -> padded to exact 50", [10, 10], [], 50, True, True)
# 6. No AI enabled -> rule-based template path honors count
run_case("No AI: templates sliced to 4", [], [], 4, False, False)
# 7. Only Ollama enabled, delivers full count
run_case("Ollama alone: 8/8", [8], [], 8, False, True)

with open("collab_test_result.txt", "w", encoding="utf-8") as f:
    for status, name, detail in results:
        f.write(f"[{status}] {name}: {detail}\n")
    f.write("\nSUMMARY: " + ("ALL PASSED" if all(s == "PASS" for s, _, _ in results) else "FAILURES PRESENT") + "\n")
print("done")