"""
AI Service Module for Interview System
Handles question generation, response analysis, and report generation

Optional AI upgrade (see gemini_ai.py for Gemini and ollama_ai.py for Ollama):
when the user has enabled and configured an AI provider in Settings, the
functions below use real AI generation.  Gemini is tried first; if it is
unavailable, Ollama (local model) is tried next; if both are unavailable,
every function transparently falls back to the built-in rule-based logic
so the system keeps working.
"""

import logging
import os

# Optional Gemini upgrade layer (same project folder). The system works
# without it -- all functions fall back to the rule-based logic below.
try:
    import gemini_ai
except ImportError:  # pragma: no cover
    gemini_ai = None

# Optional Ollama AI layer (local model, no API key needed).
# Works with EITHER:
#   1. The `ollama` PyPI package  (pip install ollama) + `ollama serve`, or
#   2. Plain REST via urllib (no extra install) -- ollama_ai.py handles both.
try:
    import ollama_ai
except ImportError:  # pragma: no cover
    ollama_ai = None

# Optional `ollama` PyPI client (user installed it: "nag install ako ng ollama").
# Imported lazily/optionally -- the app NEVER breaks when it is missing because
# ollama_ai.py also speaks raw REST via urllib.
try:
    import ollama as ollama_pkg  # type: ignore
except ImportError:  # pragma: no cover
    ollama_pkg = None

logger = logging.getLogger(__name__)

# Sample questions by system type and user role
QUESTION_TEMPLATES = {
    "basic_gathering": {
        "student": [
            {
                "text": "What is your name and what year are you in?",
                "category": "personal_info"
            },
            {
                "text": "What is your major or field of study?",
                "category": "personal_info"
            },
            {
                "text": "What are your academic goals for this semester?",
                "category": "goals"
            },
            {
                "text": "What challenges are you currently facing in your studies?",
                "category": "challenges"
            },
            {
                "text": "How do you prefer to learn new material (lectures, group work, hands-on, etc.)?",
                "category": "preferences"
            },
            {
                "text": "What resources or support would help you succeed better?",
                "category": "suggestions"
            }
        ],
        "instructor": [
            {
                "text": "What is your name and how many years have you been teaching?",
                "category": "personal_info"
            },
            {
                "text": "What courses are you currently teaching?",
                "category": "personal_info"
            },
            {
                "text": "What are your primary teaching goals this semester?",
                "category": "goals"
            },
            {
                "text": "What challenges do you face in your teaching?",
                "category": "challenges"
            },
            {
                "text": "What teaching methods work best for your students?",
                "category": "preferences"
            },
            {
                "text": "What resources or support would improve your teaching effectiveness?",
                "category": "suggestions"
            }
        ]
    },
    "information_system": {
        "administrator": [
            {
                "text": "What are the main challenges you face in managing the current system?",
                "category": "pain_points"
            },
            {
                "text": "How much time do you spend on manual data entry and management tasks?",
                "category": "workflows"
            },
            {
                "text": "What features would make system administration easier?",
                "category": "desired_features"
            },
            {
                "text": "Are there any security or data integrity issues you've encountered?",
                "category": "pain_points"
            },
            {
                "text": "How do you currently handle system backups and disaster recovery?",
                "category": "workflows"
            }
        ],
        "end_user": [
            {
                "text": "Describe your typical workflow when using this system.",
                "category": "workflows"
            },
            {
                "text": "What frustrates you most about the current system?",
                "category": "pain_points"
            },
            {
                "text": "What features do you wish this system had?",
                "category": "desired_features"
            },
            {
                "text": "How much training did you receive to use this system?",
                "category": "workflows"
            },
            {
                "text": "What would improve your productivity?",
                "category": "desired_features"
            }
        ],
        "manager": [
            {
                "text": "What key metrics do you track in the system?",
                "category": "workflows"
            },
            {
                "text": "What reporting challenges do you face?",
                "category": "pain_points"
            },
            {
                "text": "How could the system better support decision-making?",
                "category": "desired_features"
            },
            {
                "text": "Are there any integration issues with other systems?",
                "category": "pain_points"
            },
            {
                "text": "What improvements would have the most impact on your team?",
                "category": "desired_features"
            }
        ]
    },
    "crm_system": {
        "sales_team": [
            {
                "text": "How does the current CRM help or hinder your sales process?",
                "category": "pain_points"
            },
            {
                "text": "What customer information is most important for you?",
                "category": "workflows"
            },
            {
                "text": "How much time do you spend on administrative tasks versus selling?",
                "category": "pain_points"
            },
            {
                "text": "What features would help you close deals faster?",
                "category": "desired_features"
            },
            {
                "text": "How do you share customer information with your team?",
                "category": "workflows"
            }
        ],
        "support_team": [
            {
                "text": "How does the CRM support your customer service workflow?",
                "category": "workflows"
            },
            {
                "text": "What information do you need to resolve customer issues quickly?",
                "category": "desired_features"
            },
            {
                "text": "What challenges do you face with the current system?",
                "category": "pain_points"
            },
            {
                "text": "How do you track customer interactions and history?",
                "category": "workflows"
            },
            {
                "text": "What would improve customer satisfaction?",
                "category": "desired_features"
            }
        ]
    },
    "hr_system": {
        "hr_manager": [
            {
                "text": "What HR processes does the system currently support?",
                "category": "workflows"
            },
            {
                "text": "What compliance or reporting requirements are challenging?",
                "category": "pain_points"
            },
            {
                "text": "How could the system better support employee development?",
                "category": "desired_features"
            },
            {
                "text": "What data accuracy issues have you encountered?",
                "category": "pain_points"
            },
            {
                "text": "How much time do you spend on administrative tasks?",
                "category": "pain_points"
            }
        ],
        "employee": [
            {
                "text": "How easy is it to find HR policies and information in the system?",
                "category": "pain_points"
            },
            {
                "text": "What HR services do you use most frequently?",
                "category": "workflows"
            },
            {
                "text": "What would make it easier to access HR services?",
                "category": "desired_features"
            },
            {
                "text": "How clear is the company's leave and benefits policy?",
                "category": "pain_points"
            },
            {
                "text": "What improvements would enhance your employee experience?",
                "category": "desired_features"
            }
        ]
    }
}


def generate_interview_questions(system_type: str, user_role: str, max_questions: int = None,
                                 gemini_cfg: dict = None, extra_context: str = None) -> list:
    """
    Generate interview questions based on system type and user role.
    
    Args:
        system_type: Type of system (e.g., 'information_system', 'crm_system', 'hr_system')
        user_role: Role of the interviewee (e.g., 'administrator', 'end_user', 'manager')
        max_questions: Optional limit on the number of questions returned
        gemini_cfg: Optional Gemini AI config; when enabled, questions are AI-generated
        extra_context: Optional interview context (interview title / objectives) that
                       helps the AI generate more relevant questions. The final number
                       of questions always follows max_questions.
    
    Returns:
        List of question dictionaries with 'text' and 'category' keys
        """
    
    # Gemini + Ollama work TOGETHER ("magtulungan") until the requested count
    # is reached.  Whatever number the user enters becomes the target, and the
    # final list ALWAYS contains exactly that many unique questions.
    target = int(max_questions) if (max_questions and int(max_questions) > 0) else 6
    gem_cfg, oll_cfg = _normalize_ai_cfg(gemini_cfg)
    gemini_on = _gemini_enabled(gemini_cfg)
    ollama_on = _ollama_enabled(gemini_cfg)
    ai_questions = []
    seen_keys = set()

    def _collect(part):
        """Merge a generated batch into ai_questions, skipping duplicates."""
        if not part:
            return
        for q in part:
            text = str(q.get("text", "")).strip()
            key = "".join(text.lower().split())
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            ai_questions.append({"text": text, "category": q.get("category", "context")})

    # ---- Round 1: Gemini generates the full set ----
    if gemini_on:
        _collect(_gemini_questions(system_type, user_role, target, gem_cfg,
                                   extra_context=extra_context))
        logger.info("Gemini generated %d/%d interview questions (role=%s)",
                    len(ai_questions), target, user_role)

    # ---- Round 2: Ollama tops up whatever Gemini missed ----
    if len(ai_questions) < target and ollama_on:
        missing = target - len(ai_questions)
        _collect(_ollama_questions(system_type, user_role, missing, oll_cfg,
                                   extra_context=extra_context,
                                   avoid_texts=[q["text"] for q in ai_questions]))
        logger.info("Ollama topped up to %d/%d interview questions (role=%s)",
                    len(ai_questions), target, user_role)

    # ---- Round 3: Gemini tops up when Ollama was the primary and fell short ----
    if len(ai_questions) < target and gemini_on:
        missing = target - len(ai_questions)
        _collect(_gemini_questions(system_type, user_role, missing, gem_cfg,
                                   extra_context=extra_context,
                                   avoid_texts=[q["text"] for q in ai_questions]))

    if gemini_on or ollama_on:
        if len(ai_questions) >= target:
            return ai_questions[:target]
        # Both AIs together still fell short: pad from the built-in pool so
        # the user ALWAYS receives exactly the number they asked for.
        missing = target - len(ai_questions)
        pad = _fallback_question_pool(system_type, user_role, missing,
                                      avoid_texts=[q["text"] for q in ai_questions])
        logger.info("AI providers produced %d/%d questions; padding with %d built-in questions",
                    len(ai_questions), target, len(pad))
        _collect(pad)
        return ai_questions[:target]

    # Normalize inputs (no AI provider enabled -- rule-based behavior)
    system_type = system_type.lower().replace(" ", "_")
    user_role = user_role.lower().replace(" ", "_")

    # Get questions for the system type
    if system_type in QUESTION_TEMPLATES:
        system_questions = QUESTION_TEMPLATES[system_type]
        
        # Get questions for the specific role, or return general questions
        if user_role in system_questions:
            questions = system_questions[user_role]
        else:
            # Return first available role's questions as fallback
            first_role = list(system_questions.keys())[0]
            questions = system_questions[first_role]
        
        if target and target > 0:
            return questions[:target]
        return questions
    else:
        # Return generic questions if system type not found
        return get_generic_questions()[:target] if target else get_generic_questions()


def get_generic_questions() -> list:
    """Return generic interview questions for any system."""
    return [
        {"text": "What is your primary role with this system?", "category": "context"},
        {"text": "What are the main challenges you face?", "category": "pain_points"},
        {"text": "What features would be most valuable?", "category": "desired_features"},
        {"text": "How much time do you spend on this system daily?", "category": "workflows"},
        {"text": "What improvements would have the most impact?", "category": "desired_features"},
        {"text": "How did you first learn about this system?", "category": "context"},
        {"text": "What do you like most about the current system?", "category": "preferences"},
        {"text": "What do you like least about the current system?", "category": "pain_points"},
        {"text": "How does this system affect your daily workflow?", "category": "workflows"},
        {"text": "What training or support did you receive?", "category": "context"},
        {"text": "What additional training would help you?", "category": "suggestions"},
        {"text": "How reliable is the system in your experience?", "category": "challenges"},
        {"text": "What workarounds have you developed?", "category": "workflows"},
        {"text": "What information do you need that is hard to find?", "category": "pain_points"},
        {"text": "How could communication be improved?", "category": "suggestions"},
        {"text": "What are your goals when using this system?", "category": "goals"},
        {"text": "How does the system compare to alternatives you have used?", "category": "preferences"},
        {"text": "What security or privacy concerns do you have?", "category": "challenges"},
        {"text": "What reporting features do you need?", "category": "desired_features"},
        {"text": "What would make your job easier?", "category": "suggestions"},
        {"text": "How do you currently solve problems with the system?", "category": "workflows"},
        {"text": "What feedback have you given before?", "category": "context"},
        {"text": "How could the system be more accessible?", "category": "suggestions"},
        {"text": "What integrations with other tools would help?", "category": "desired_features"},
        {"text": "What is your biggest frustration?", "category": "pain_points"},
        {"text": "How do you measure success with this system?", "category": "goals"},
        {"text": "What support resources do you wish existed?", "category": "suggestions"},
        {"text": "How has the system changed how you work?", "category": "workflows"},
        {"text": "What would you tell the developers?", "category": "suggestions"},
        {"text": "What future improvements are you hoping for?", "category": "desired_features"},
        {"text": "How well does the system meet your needs today?", "category": "goals"},
        {"text": "What manual processes would you automate?", "category": "suggestions"},
        {"text": "How do errors or downtime affect your work?", "category": "pain_points"},
        {"text": "What data or analytics would help you?", "category": "desired_features"},
        {"text": "How could onboarding new users be improved?", "category": "suggestions"},
        {"text": "What collaboration features do you need?", "category": "desired_features"},
        {"text": "How do you share information with colleagues?", "category": "workflows"},
        {"text": "What compliance or policy issues affect you?", "category": "challenges"},
        {"text": "What mobile or remote access do you need?", "category": "desired_features"},
        {"text": "How could notifications be improved?", "category": "suggestions"},
        {"text": "What customization options would you value?", "category": "preferences"},
        {"text": "How do you handle peak usage times?", "category": "workflows"},
        {"text": "What backup or recovery options do you need?", "category": "desired_features"},
        {"text": "How could search be improved?", "category": "suggestions"},
        {"text": "What accessibility features matter to you?", "category": "desired_features"},
        {"text": "How could the interface be simplified?", "category": "suggestions"},
        {"text": "What is missing from the current system?", "category": "desired_features"},
        {"text": "How do you prioritize your tasks with this system?", "category": "workflows"},
        {"text": "What performance issues have you noticed?", "category": "pain_points"},
        {"text": "How could reporting be streamlined?", "category": "suggestions"},
        {"text": "What other stakeholders should be involved?", "category": "context"}
    ]


def analyze_responses(responses: list,
                      pain_indicators: list = None,
                      feature_indicators: list = None,
                      workflow_indicators: list = None,
                      gemini_cfg: dict = None) -> list:
    """
    Analyze interview responses to extract key points.
    
    Args:
        responses: List of response texts
        pain_indicators: Optional custom list of pain-point keywords
        feature_indicators: Optional custom list of desired-feature keywords
        workflow_indicators: Optional custom list of workflow keywords
        gemini_cfg: Optional Gemini AI config; when enabled, key points are AI-extracted
    
    Returns:
        List of key points extracted from responses
    """
    
            # Gemini AI upgrade: extract key points with real AI when configured
    if _gemini_enabled(gemini_cfg) and responses:
        gem_cfg, _ = _normalize_ai_cfg(gemini_cfg)
        ai_points = _gemini_key_points(responses, gem_cfg)
        if ai_points:
            return ai_points
        logger.info("Gemini key-point extraction unavailable; using keyword matching")

    # Ollama AI alternative: local model, no API key required
    if _ollama_enabled(gemini_cfg) and responses:
        _, oll_cfg = _normalize_ai_cfg(gemini_cfg)
        ai_points = _ollama_key_points(responses, oll_cfg)
        if ai_points:
            return ai_points
        logger.info("Ollama key-point extraction unavailable; using keyword matching")
    
    key_points = []
    
    for response in responses:
        if not response:
            continue
            
        # Simple keyword extraction (can be enhanced with NLP)
        keywords = extract_keywords(response, pain_indicators=pain_indicators,
                                    feature_indicators=feature_indicators,
                                    workflow_indicators=workflow_indicators)
        key_points.extend(keywords)
    
    # Remove duplicates and return
    return list(set(key_points))


def extract_keywords(text: str,
                     pain_indicators: list = None,
                     feature_indicators: list = None,
                     workflow_indicators: list = None) -> list:
    """
    Extract keywords from response text.
    
    Args:
        text: Response text
        pain_indicators: Optional custom pain-point keywords (defaults used if None)
        feature_indicators: Optional custom desired-feature keywords (defaults used if None)
        workflow_indicators: Optional custom workflow keywords (defaults used if None)
    
    Returns:
        List of extracted keywords/phrases
    """
    
    # Simple keyword extraction based on common patterns
    keywords = []
    
    # Look for common phrases indicating pain points
    if pain_indicators is None:
        pain_indicators = ["challenge", "difficult", "frustrat", "problem", "issue", "slow", "manual"]
    for indicator in pain_indicators:
        if indicator.lower() in text.lower():
            keywords.append(f"Pain point: {text[:50]}...")
            break
    
    # Look for desired features
    if feature_indicators is None:
        feature_indicators = ["feature", "would help", "need", "should", "could", "want"]
    for indicator in feature_indicators:
        if indicator.lower() in text.lower():
            keywords.append(f"Desired feature: {text[:50]}...")
            break
    
    # Extract workflow mentions
    if workflow_indicators is None:
        workflow_indicators = ["process", "workflow", "step", "then", "next"]
    for indicator in workflow_indicators:
        if indicator.lower() in text.lower():
            keywords.append(f"Workflow: {text[:50]}...")
            break
    
    return keywords


# ============ SUGGESTED SOLUTIONS ENGINE ============
# Keyword rules that map identified findings to actionable solution recommendations
SOLUTION_RULES = [
    (["slow", "delay", "lag", "long time", "time-consuming", "hours", "takes too long", "waiting"],
     "Optimize this process for speed: streamline approval steps, remove redundant tasks, and automate the slowest part of the workflow."),
    (["manual", "paper", "encode", "typing", "repetitive", "duplicate"],
     "Digitize this manual task: introduce electronic forms with validation and auto-fill so information is entered only once."),
    (["difficult", "hard", "confusing", "complicated", "complex", "unclear"],
     "Simplify the user experience: redesign the workflow into clear step-by-step screens, add tooltips, and provide a short user guide or training."),
    (["communicat", "coordinat", "notif", "announce", "inform", "update"],
     "Improve communication: add automated notifications, a central announcement board, and real-time status updates."),
    (["track", "monitor", "record", "lost", "missing", "accuracy", "error", "mistake"],
     "Strengthen data tracking: centralize records in a single database with validation rules and audit trails to prevent errors and lost data."),
    (["access", "offline", "no internet", "device", "mobile", "computer", "laboratory", "equipment"],
     "Improve accessibility: provide mobile-friendly access and offline-capable workflows where devices or connectivity are limited."),
    (["security", "privacy", "confidential", "unauthorized", "hack"],
     "Enhance security: enforce role-based access control, protect sensitive data, and schedule regular security reviews."),
    (["queue", "line", "crowd", "schedule", "booking", "reserve", "conflict"],
     "Reduce waiting and conflicts: implement online scheduling/queueing with automatic confirmations and calendar reminders."),
    (["cost", "expensive", "budget", "afford", "money", "fee"],
     "Review resource allocation: prioritize low-cost digital alternatives and phase the rollout based on available budget."),
    (["feedback", "evaluation", "survey", "assessment", "rating"],
     "Institutionalize feedback: build in an evaluation mechanism that produces periodic reports for decision-makers."),
]
DEFAULT_SOLUTION = ("Conduct a short follow-up session with the stakeholders involved to validate this finding, "
                    "design a targeted improvement, and measure its impact after deployment.")


def generate_suggested_solutions(pain_points: list,
                                 desired_features: list,
                                 recurring_issues: list) -> list:
    """
    Map identified pain points, recurring issues, and desired features to
    concrete, actionable suggested solutions.

    Args:
        pain_points: List of identified pain point texts
        desired_features: List of requested feature texts
        recurring_issues: List of recurring issue texts

    Returns:
        List of dicts: {source, issue, solution}
    """
    suggestions = []
    seen = set()

    items = ([("Pain Point", p) for p in (pain_points or [])] +
             [("Recurring Issue", r) for r in (recurring_issues or [])])

    for source, text in items:
        if not text or text in seen:
            continue
        seen.add(text)
        lower = text.lower()
        solution = DEFAULT_SOLUTION
        for keywords, sol in SOLUTION_RULES:
            if any(k in lower for k in keywords):
                solution = sol
                break
        suggestions.append({"source": source, "issue": text, "solution": solution})

    for feature in (desired_features or []):
        if feature and feature not in seen:
            seen.add(feature)
            suggestions.append({
                "source": "Requested Feature",
                "issue": feature,
                "solution": ("Plan this requested capability: define the requirement, prioritize it by impact "
                             "versus effort, and include it in the development roadmap.")
            })

    return suggestions


# ============ AUTOMATED RECOMMENDATIONS ENGINE ============
# Maps interview findings to prioritized, actionable insights:
# "What should the organization DO next?" (beyond the per-issue solutions).

RECOMMENDATION_RULES = [
    (["slow", "delay", "lag", "long time", "time-consuming", "hours", "takes too long", "waiting"],
     "Automate the Slow Process",
     "Map the current workflow, identify the slowest step, and automate or remove it. Set a measurable target (e.g., cut processing time by 50%) and review progress after one month.",
     "High"),
    (["manual", "paper", "encode", "typing", "repetitive"],
     "Digitize Manual / Paper-Based Work",
     "Replace paper forms with validated digital forms with auto-fill so information is captured once and shared across offices instead of re-typed.",
     "High"),
    (["difficult", "hard", "confusing", "complicated", "complex", "unclear"],
     "Simplify the User Experience",
     "Redesign the confusing screens into clear step-by-step flows, add tooltips and a short user guide, then run hands-on training for affected users.",
     "Medium"),
    (["communicat", "coordinat", "notif", "announce", "inform", "update"],
     "Centralize Communication",
     "Add automated notifications and a central announcement board so stakeholders receive real-time updates instead of relying on word-of-mouth.",
     "Medium"),
    (["track", "monitor", "record", "lost", "missing", "accuracy", "error", "mistake", "duplicate"],
     "Strengthen Data Tracking & Integrity",
     "Centralize records in one validated database with audit trails and scheduled backups to stop missing or duplicated data.",
     "High"),
    (["access", "offline", "no internet", "device", "mobile", "computer", "laboratory", "equipment"],
     "Improve Accessibility",
     "Provide mobile-friendly access and offline-capable workflows, and prioritize shared devices/labs for the users who need them most.",
     "Medium"),
    (["security", "privacy", "confidential", "unauthorized", "hack"],
     "Enhance Security & Privacy",
     "Enforce role-based access control on sensitive data and schedule a periodic security review with the IT office.",
     "High"),
    (["queue", "line", "crowd", "schedule", "booking", "reserve", "conflict"],
     "Reduce Waiting & Scheduling Conflicts",
     "Introduce online scheduling/queueing with automatic confirmations and calendar reminders to eliminate double-booking and long lines.",
     "Medium"),
    (["cost", "expensive", "budget", "afford", "money", "fee"],
     "Optimize Resource Allocation",
     "Prioritize low-cost digital alternatives, phase the rollout by budget, and track cost savings per improvement implemented.",
     "Low"),
    (["feedback", "evaluation", "survey", "assessment", "rating"],
     "Institutionalize Feedback",
     "Build a regular feedback loop (short surveys after each process) and review the results monthly with the process owners.",
     "Low"),
]

DEFAULT_RECOMMENDATION = (
    "Follow up with the interviewed stakeholders to validate this finding, "
    "design a targeted improvement, and measure its impact after deployment."
)



def generate_recommendations(pain_points: list,
                             desired_features: list,
                             recurring_issues: list,
                             user_role: str = None,
                             gemini_cfg: dict = None) -> list:
    """
    Generate prioritized, actionable recommendations from interview findings.

    Args:
        pain_points: List of identified pain point texts
        desired_features: List of requested feature texts
        recurring_issues: List of recurring issue texts
        user_role: Optional stakeholder category (e.g., 'Student', 'Dean') used to tailor the closing advice
        gemini_cfg: Optional Gemini AI config; when enabled, recommendations are AI-generated

    Returns:
        List of dicts: {title, action, priority, based_on}
    """
    # Gemini AI upgrade: produce AI recommendations when configured
    if _ai_enabled(gemini_cfg):
        ai_recs = _gemini_recommendations(pain_points, desired_features,
                                          recurring_issues, user_role, gemini_cfg)
        if ai_recs:
            logger.info("Gemini generated %d recommendations", len(ai_recs))
            return ai_recs
        logger.info("Gemini recommendations unavailable; using rule-based engine")

    # Ollama AI alternative: local model, no API key required
    if _ollama_enabled(gemini_cfg):
        _, oll_cfg = _normalize_ai_cfg(gemini_cfg)
        ai_recs = _ollama_recommendations(pain_points, desired_features,
                                          recurring_issues, user_role, oll_cfg)
        if ai_recs:
            logger.info("Ollama generated %d recommendations", len(ai_recs))
            return ai_recs
        logger.info("Ollama recommendations unavailable; using rule-based engine")

    recommendations = []
    seen = set()

    def add_recommendation(title, action, priority, based_on):
        key = title.lower()
        if key in seen:
            return
        seen.add(key)
        recommendations.append({
            "title": title,
            "action": action,
            "priority": priority,
            "based_on": based_on,
        })

    items = (
        [("Pain Point", p) for p in (pain_points or [])] +
        [("Recurring Issue", r) for r in (recurring_issues or [])]
    )
    for source, text in items:
        if not text:
            continue
        lower = text.lower()
        matched = False
        for keywords, title, action, priority in RECOMMENDATION_RULES:
            if any(k in lower for k in keywords):
                add_recommendation(title, action, priority, f"{source}: {text}")
                matched = True
                break
        if not matched:
            add_recommendation("Validate & Prioritize This Finding", DEFAULT_RECOMMENDATION, "Medium",
                               f"{source}: {text}")

    for feature in (desired_features or []):
        if not feature:
            continue
        lower = feature.lower()
        matched = False
        for keywords, title, action, priority in RECOMMENDATION_RULES:
            if any(k in lower for k in keywords):
                add_recommendation(title, action, priority, f"Requested Feature: {feature}")
                matched = True
                break
        if not matched:
            add_recommendation(
                "Plan the Requested Capability",
                ("Define this requested feature as a requirement, prioritize it by impact versus effort, "
                 "and include it in the development roadmap."),
                "Medium",
                f"Requested Feature: {feature}"
            )

    # Sort by priority: High -> Medium -> Low
    order = {"High": 0, "Medium": 1, "Low": 2}
    recommendations.sort(key=lambda r: order.get(r["priority"], 3))

    # Tailored closing advice based on the stakeholder category interviewed
    if recommendations and user_role:
        add_recommendation(
            f"Share Results with the {str(user_role).strip()} Group",
            ("Present these findings back to the interviewed stakeholders to confirm the analysis "
             "and co-plan the next steps with them."),
            "Low",
            f"Stakeholder category: {user_role}"
        )

    return recommendations



def generate_summary(responses: list, gemini_cfg: dict = None) -> dict:
    """
    Generate comprehensive summary from interview responses.
    
    Args:
        responses: List of all response texts from interview
        gemini_cfg: Optional Gemini AI config; when enabled, the summary is AI-generated
    
    Returns:
        Dictionary containing summary, pain_points, desired_features, and recurring_issues
    """
    
    # Gemini AI upgrade: produce an AI-written analysis when configured
    if _ai_enabled(gemini_cfg) and responses:
        ai_summary = _gemini_summary(responses, gemini_cfg)
        if ai_summary:
            logger.info("Gemini generated interview summary (%d responses)", len(responses))
            return ai_summary
        logger.info("Gemini summary unavailable; using rule-based analysis")

    # Ollama AI alternative: local model, no API key required
    if _ollama_enabled(gemini_cfg) and responses:
        _, oll_cfg = _normalize_ai_cfg(gemini_cfg)
        ai_summary = _ollama_summary(responses, oll_cfg)
        if ai_summary:
            logger.info("Ollama generated interview summary (%d responses)", len(responses))
            return ai_summary
        logger.info("Ollama summary unavailable; using rule-based analysis")
    
    if not responses:
        return {
            'summary': 'No responses recorded.',
            'pain_points': [],
            'desired_features': [],
            'recurring_issues': [],
            'suggested_solutions': []
        }
    
    # Categorize responses
    pain_points = []
    desired_features = []
    recurring_issues = []
    workflow_insights = []
    
    for response in responses:
        if not response:
            continue
            
        lower_response = response.lower()
        
        # Identify pain points
        if any(word in lower_response for word in ["difficult", "challenge", "frustrat", "problem", "issue", "slow"]):
            pain_points.append(response[:100])
        
        # Identify desired features
        if any(word in lower_response for word in ["feature", "would help", "need", "should", "want", "wish"]):
            desired_features.append(response[:100])
        
        # Identify recurring patterns
        if any(word in lower_response for word in ["always", "every", "often", "frequently", "repeatedly"]):
            recurring_issues.append(response[:100])
        
        # Workflow insights
        if any(word in lower_response for word in ["process", "workflow", "procedure", "step", "then"]):
            workflow_insights.append(response[:100])
    
    # Create comprehensive summary
    summary_text = f"""
Interview Summary Report

Total Responses Analyzed: {len(responses)}

Key Findings:
- Identified {len(pain_points)} pain points
- Found {len(desired_features)} potential improvements
- Noted {len(recurring_issues)} recurring issues
- Documented {len(workflow_insights)} workflow insights

Recommendations:
1. Address the identified pain points to improve user satisfaction
2. Prioritize desired features based on impact and frequency
3. Investigate recurring issues for systemic improvements
4. Optimize workflows to increase efficiency
5. Review the AI-suggested solutions mapped to each finding and prioritize them by impact
"""
    
    suggested_solutions = generate_suggested_solutions(pain_points, desired_features, recurring_issues)

    return {
        'summary': summary_text,
        'pain_points': pain_points,
        'desired_features': desired_features,
        'recurring_issues': recurring_issues,
        'suggested_solutions': suggested_solutions
    }


def compare_responses_across_users(all_interview_data: dict) -> dict:
    """
    Compare responses across different interviewees to identify patterns.
    
    Args:
        all_interview_data: Dictionary of interview data from multiple users
    
    Returns:
        Dictionary of analysis comparing responses
    """
    
    common_issues = {}
    common_features = {}
    
    # This would be enhanced with actual data analysis
    return {
        'common_pain_points': common_issues,
        'common_desired_features': common_features
    }


# ============ GEMINI AI UPGRADE LAYER ============
# When a Gemini API key is configured in Settings, the functions above use
# real AI generation. Every helper below returns None on any problem so the
# caller falls back to the rule-based logic above — nothing ever breaks.

ALLOWED_QUESTION_CATEGORIES = {
    "personal_info", "goals", "challenges", "preferences", "suggestions",
    "pain_points", "workflows", "desired_features", "context",
}


def _ai_enabled(gemini_cfg) -> bool:
    """True when Gemini is configured, enabled, and currently available."""
    if not gemini_cfg or gemini_ai is None:
        return False
    if not gemini_cfg.get("enabled") or not gemini_cfg.get("api_key"):
        return False
    return gemini_ai.is_available()


def _normalize_ai_cfg(cfg) -> tuple:
    """
    Split a combined AI config into (gemini_cfg, ollama_cfg).

    app.py's build_gemini_cfg() returns the Gemini config dict and attaches
    the Ollama config under the optional "ollama" key, e.g.::

        {"enabled": True, "api_key": "...", "model": "...", "source": "...",
         "ollama": {"enabled": False, "url": "...", "model": "..."}}

    Returns a tuple so callers can route each provider to its own helpers.
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    oll_cfg = cfg.get("ollama")
    gem_cfg = {k: v for k, v in cfg.items() if k != "ollama"}
    return gem_cfg, (oll_cfg if isinstance(oll_cfg, dict) else {})


def _gemini_enabled(cfg) -> bool:
    """True when the Gemini part of the combined AI config is active."""
    gem_cfg, _ = _normalize_ai_cfg(cfg)
    return _ai_enabled(gem_cfg)


def _ollama_enabled(cfg) -> bool:
    """True when the Ollama part of the combined AI config is active."""
    _, oll_cfg = _normalize_ai_cfg(cfg)
    if ollama_ai is None or not oll_cfg.get("enabled"):
        return False
    return ollama_ai.is_available()



def _numbered_texts(responses: list) -> str:
    """Format response texts as a numbered list for prompts."""
    return "\n".join(f"{i + 1}. {str(r).strip()}" for i, r in enumerate(responses) if r and str(r).strip())


def _gemini_questions(system_type: str, user_role: str, max_questions: int, gemini_cfg: dict,
                      extra_context: str = None, avoid_texts: list = None):
    """AI-generate interview questions. Returns list of {text, category} or None."""
    try:
        count = int(max_questions) if (max_questions and int(max_questions) > 0) else 6
        # A JSON question entry costs ~60-90 tokens.  The old estimate
        # (count * 30 + 500) truncated large sets, so only a few (often just
        # one) questions survived the JSON parse.  Be generous instead.
        tokens_needed = min(8192, max(2048, count * 90 + 1000))
        context_line = ""
        if extra_context and str(extra_context).strip():
            context_line = f"- Interview context: {str(extra_context).strip()}\n"
        avoid_line = ""
        if avoid_texts:
            listed = "\n".join(f"- {t}" for t in avoid_texts[:40] if t and str(t).strip())
            if listed:
                avoid_line = f"\nDo NOT repeat or rephrase any of these existing questions:\n{listed}\n"
        prompt = f"""You are an expert systems analyst preparing stakeholder interview questions.

Context:
- System being studied: {str(system_type).replace('_', ' ')}
- Stakeholder role: {str(user_role).replace('_', ' ')}
{context_line}{avoid_line}
IMPORTANT: Generate EXACTLY {count} open-ended interview questions SPECIFICALLY tailored for a "{str(user_role).replace('_', ' ')}". Count your questions before answering: the JSON array MUST contain exactly {count} items — no more, no fewer. The questions must be relevant to their daily work, challenges, and experiences in their role.

Topics to cover: how they use the system, challenges they face, desired improvements, workflows, pain points, goals, and suggestions. Every question must be unique (no duplicates). Questions must be easy to understand and answerable by a non-technical person.

Each question must have a "category" chosen from exactly one of: personal_info, goals, challenges, preferences, suggestions, pain_points, workflows, desired_features, context.

Respond with ONLY a JSON array, no extra text, in this exact format:
[{{"text": "Question text here?", "category": "pain_points"}}]"""
        raw = gemini_ai.call_gemini(prompt, api_key=gemini_cfg["api_key"], model=gemini_cfg.get("model"), max_output_tokens=tokens_needed)
        data = gemini_ai.parse_json_block(raw)
        if not isinstance(data, list):
            return None
        questions = []
        for item in data:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            category = str(item.get("category", "context")).strip().lower().replace(" ", "_")
            if category not in ALLOWED_QUESTION_CATEGORIES:
                category = "context"
            questions.append({"text": text, "category": category})
        questions = questions[:count]
        return questions or None
    except Exception as exc:
        logger.warning("Gemini question generation error: %s", exc)
        return None


def _gemini_key_points(responses: list, gemini_cfg: dict):
    """AI-extract concise key points. Returns list of strings or None."""
    try:
        prompt = f"""You are analyzing interview responses about a system.

Responses:
{_numbered_texts(responses)}

Extract the most important key points. Each key point must be one short concise phrase (maximum 12 words) describing a pain point, desired feature, workflow detail, or notable insight.

Respond with ONLY a JSON array of strings, no extra text:
["key point one", "key point two"]"""
        raw = gemini_ai.call_gemini(prompt, api_key=gemini_cfg["api_key"], model=gemini_cfg.get("model"))
        data = gemini_ai.parse_json_block(raw)
        if not isinstance(data, list):
            return None
        points = [str(point).strip() for point in data if str(point).strip()]
        return points or None
    except Exception as exc:
        logger.warning("Gemini key-point extraction error: %s", exc)
        return None


def _gemini_summary(responses: list, gemini_cfg: dict):
    """AI-generate the full interview analysis. Returns the standard summary dict or None."""
    try:
        prompt = f"""You are a systems analyst summarizing stakeholder interview responses.

Responses:
{_numbered_texts(responses)}

Produce an analysis with:
- "summary": a well-written multi-paragraph plain-text report describing the overall findings, written for decision-makers.
- "pain_points": list of short phrases (max 12 words each) describing problems users face
- "desired_features": list of short phrases describing improvements or features users want
- "recurring_issues": list of short phrases describing problems that repeat across responses
- "suggested_solutions": list of objects {{"source": "Pain Point" or "Recurring Issue" or "Requested Feature", "issue": "<matching issue text from above>", "solution": "1-2 sentence concrete, actionable solution"}}

Respond with ONLY a JSON object, no extra text, exactly in this format:
{{"summary": "...", "pain_points": ["..."], "desired_features": ["..."], "recurring_issues": ["..."], "suggested_solutions": [{{"source": "Pain Point", "issue": "...", "solution": "..."}}]}}"""
        raw = gemini_ai.call_gemini(prompt, api_key=gemini_cfg["api_key"],
                                    model=gemini_cfg.get("model"), max_output_tokens=3072)
        data = gemini_ai.parse_json_block(raw)
        if not isinstance(data, dict):
            return None

        def clean_str_list(value):
            if not isinstance(value, list):
                return []
            return [str(item).strip() for item in value if str(item).strip()]

        solutions = []
        for item in (data.get("suggested_solutions") or []):
            if not isinstance(item, dict):
                continue
            issue = str(item.get("issue", "")).strip()
            solution = str(item.get("solution", "")).strip()
            source = str(item.get("source", "Pain Point")).strip() or "Pain Point"
            if issue and solution:
                solutions.append({"source": source, "issue": issue, "solution": solution})

        summary_text = str(data.get("summary", "")).strip()
        if not summary_text:
            return None

        return {
            "summary": summary_text,
            "pain_points": clean_str_list(data.get("pain_points")),
            "desired_features": clean_str_list(data.get("desired_features")),
            "recurring_issues": clean_str_list(data.get("recurring_issues")),
            "suggested_solutions": solutions,
        }
    except Exception as exc:
        logger.warning("Gemini summary error: %s", exc)
        return None


def _gemini_recommendations(pain_points: list, desired_features: list,
                            recurring_issues: list, user_role: str, gemini_cfg: dict):
    """AI-generate prioritized recommendations. Returns list of {title, action, priority, based_on} or None."""
    try:
        findings = (
            [f"Pain point: {p}" for p in (pain_points or []) if p] +
            [f"Requested feature: {f}" for f in (desired_features or []) if f] +
            [f"Recurring issue: {r}" for r in (recurring_issues or []) if r]
        )
        if not findings:
            return None
        numbered = "\n".join(f"- {f}" for f in findings)
        role_line = f"\nThe interviewed stakeholder category is: {user_role}.\n" if user_role else ""
        prompt = f"""You are an IT consultant turning stakeholder interview findings into prioritized recommendations.

Findings:
{numbered}
{role_line}
For each distinct finding (merge duplicates), give ONE actionable recommendation. Each recommendation needs:
- "title": short title of the action (e.g. "Automate the Slow Process")
- "action": 1-2 sentence concrete next step the organization should take
- "priority": exactly one of "High", "Medium", "Low"
- "based_on": the finding text this recommendation came from

Respond with ONLY a JSON array, no extra text, exactly in this format:
[{{"title": "...", "action": "...", "priority": "High", "based_on": "..."}}]"""
        raw = gemini_ai.call_gemini(prompt, api_key=gemini_cfg["api_key"],
                                    model=gemini_cfg.get("model"), max_output_tokens=3072)
        data = gemini_ai.parse_json_block(raw)
        if not isinstance(data, list) or not data:
            return None

        recommendations = []
        seen = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            action = str(item.get("action", "")).strip()
            if not title or not action or title.lower() in seen:
                continue
            seen.add(title.lower())
            priority = str(item.get("priority", "Medium")).strip().capitalize()
            if priority not in ("High", "Medium", "Low"):
                priority = "Medium"
            based_on = str(item.get("based_on", "")).strip() or title
            recommendations.append({
                "title": title,
                "action": action,
                "priority": priority,
                "based_on": based_on,
            })
        if not recommendations:
            return None
        order = {"High": 0, "Medium": 1, "Low": 2}
        recommendations.sort(key=lambda rec: order.get(rec["priority"], 3))
        return recommendations
    except Exception as exc:
        logger.warning("Gemini recommendations error: %s", exc)
        return None


# ============ OLLAMA AI LAYER (LOCAL MODEL, NO API KEY) ============
# When an Ollama server (e.g. http://localhost:11434) is configured in
# Settings, these helpers generate the same AI results using a LOCAL model.
# They are tried after Gemini and always return None on any problem so the
# caller falls back to the rule-based logic — nothing ever breaks.

def _ollama_call(prompt: str, oll_cfg: dict, json_mode: bool = True,
                 max_output_tokens: int = None):
    """Send a prompt through ollama_ai.call_ollama. Returns text or raises."""
    tokens = max_output_tokens or ollama_ai.MAX_OUTPUT_TOKENS
    return ollama_ai.call_ollama(
        prompt,
        url=oll_cfg.get("url"),
        model=oll_cfg.get("model"),
        json_mode=json_mode,
        max_output_tokens=tokens,
    )


def _clean_question_list(data, max_questions: int = None):
    """Validate/normalize an AI question list. Returns list of dicts or None.

    Tolerant of small local models: accepts a single question object when an
    array was requested, and coerces plain strings into question dicts.
    """
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return None
    questions = []
    for item in data:
        if isinstance(item, str) and item.strip():
            item = {"text": item.strip()}
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        category = str(item.get("category", "context")).strip().lower().replace(" ", "_")
        if category not in ALLOWED_QUESTION_CATEGORIES:
            category = "context"
        questions.append({"text": text, "category": category})
    if max_questions and max_questions > 0:
        questions = questions[:max_questions]
    return questions or None


def _fallback_question_pool(system_type: str, user_role: str, count: int,
                            avoid_texts: list = None) -> list:
    """Built-in question pool used to guarantee the EXACT requested count.

    Only used when both AI providers together produced fewer questions than
    the user asked for.  It combines the role-specific template questions and
    the generic pool, skipping anything already generated.  For very large
    counts it adds unique role-tailored follow-ups as a last resort.
    """
    if count <= 0:
        return []
    role_display = str(user_role).replace("_", " ").strip() or "stakeholder"
    avoid = {"".join(str(t).lower().split()) for t in (avoid_texts or []) if t}

    pool = []
    templates = QUESTION_TEMPLATES.get(str(system_type).lower().replace(" ", "_"), {})
    role_key = str(user_role).lower().replace(" ", "_")
    if role_key in templates:
        pool.extend(templates[role_key])
    else:
        for role_questions in templates.values():
            pool.extend(role_questions)
    pool.extend(get_generic_questions())

    out = []
    for q in pool:
        text = str(q.get("text", "")).strip()
        if not text:
            continue
        key = "".join(text.lower().split())
        if key in avoid:
            continue
        avoid.add(key)
        out.append({"text": text, "category": q.get("category", "context")})
        if len(out) >= count:
            break
    angles = [("daily workflow", "workflows"), ("biggest challenge", "challenges"),
              ("main goal", "goals"), ("process improvement", "suggestions"),
              ("system features", "desired_features"), ("overall experience", "preferences")]
    idx = 1
    while len(out) < count:
        angle, category = angles[(idx - 1) % len(angles)]
        out.append({"text": f"Follow-up {idx}: As a {role_display}, what else about your {angle} would you like to improve, and why?",
                    "category": category})
        idx += 1
    return out



def _ollama_questions(system_type: str, user_role: str, max_questions: int,
                      oll_cfg: dict, extra_context: str = None, avoid_texts: list = None):
    """AI-generate interview questions via a local Ollama model.

    Small local models often return fewer questions than requested (sometimes
    just ONE) or stop early.  To make Ollama and Gemini "magtulungan" and to
    guarantee the requested count, this keeps asking for the REMAINING number
    in small batches (<= 15 per call) until the target is reached (max 4
    rounds).  Returns a list of question dicts or None when Ollama is unusable.
    """
    if ollama_ai is None:
        return None
    try:
        want = int(max_questions) if (max_questions and int(max_questions) > 0) else 5
        context_line = ""
        if extra_context and str(extra_context).strip():
            context_line = f"\nInterview context: {str(extra_context).strip()}\n"
        base_avoid = [str(t).strip() for t in (avoid_texts or []) if t and str(t).strip()]
        collected = []
        seen_keys = {"".join(t.lower().split()) for t in base_avoid}
        for round_no in range(4):
            missing = want - len(collected)
            if missing <= 0:
                break
            ask = min(missing, 15)  # small local models cope better in batches of <= 15
            tokens_needed = min(8192, max(1024, ask * 300))
            shown = (base_avoid + [q["text"] for q in collected])[:40]
            avoid_line = ""
            if shown:
                listed = "\n".join(f"- {t}" for t in shown)
                avoid_line = f"\nDo NOT repeat or rephrase any of these questions:\n{listed}\n"
            prompt = f"""You are preparing questions for a requirements-elicitation interview about a {str(system_type).replace('_', ' ')}.
The interviewee category is: {user_role}.{context_line}{avoid_line}
Generate EXACTLY {ask} DIFFERENT open-ended interview questions covering goals, challenges, workflows, and desired features. Count your questions: the JSON array MUST contain exactly {ask} items, and every question must be unique.

Respond with ONLY a JSON array, no extra text, in this exact format:
[{{"text": "Question text here?", "category": "pain_points"}}]"""
            try:
                raw = _ollama_call(prompt, oll_cfg, json_mode=True, max_output_tokens=tokens_needed)
                data = ollama_ai.parse_json_block(raw)
            except Exception:
                # Some small models reject format=json (HTTP 400); retry as
                # plain text so parsing still gets a chance.
                raw = _ollama_call(prompt, oll_cfg, json_mode=False, max_output_tokens=tokens_needed)
                data = ollama_ai.parse_json_block(raw)
            batch = _clean_question_list(data, ask) or []
            added = 0
            for q in batch:
                key = "".join(str(q.get("text", "")).lower().split())
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)
                collected.append({"text": str(q.get("text", "")).strip(),
                                  "category": q.get("category", "context")})
                added += 1
            if added == 0:
                break  # the model keeps repeating itself; stop asking
        return collected or None
    except Exception as exc:
        logger.warning("Ollama question generation error: %s", exc)
        return None


def _ollama_key_points(responses: list, oll_cfg: dict):
    """AI-extract concise key points via a local Ollama model. Returns list of strings or None."""
    if ollama_ai is None:
        return None
    try:
        prompt = f"""You are analyzing interview responses about a system.

Responses:
{_numbered_texts(responses)}

Extract the most important key points. Each key point must be one short concise phrase (maximum 12 words) describing a pain point, desired feature, workflow detail, or notable insight.

Respond with ONLY a JSON array of strings, no extra text:
["key point one", "key point two"]"""
        raw = _ollama_call(prompt, oll_cfg, json_mode=True)
        data = ollama_ai.parse_json_block(raw)
        # Tolerate small local models: they may return a single string, a
        # plain object, or {"key_points": [...]} instead of a string array.
        if isinstance(data, str):
            data = [data]
        if isinstance(data, dict):
            for value in data.values():
                if isinstance(value, list):
                    data = value
                    break
            else:
                data = [str(v).strip() for v in data.values() if str(v).strip()]
        if not isinstance(data, list):
            return None
        points = [str(point).strip() for point in data if str(point).strip()]
        return points or None
    except Exception as exc:
        logger.warning("Ollama key-point extraction error: %s", exc)
        return None


def _ollama_summary(responses: list, oll_cfg: dict):
    """AI-generate the full interview analysis via a local Ollama model. Returns the standard summary dict or None."""
    if ollama_ai is None:
        return None
    try:
        prompt = f"""You are a systems analyst summarizing stakeholder interview responses.

Responses:
{_numbered_texts(responses)}

Produce an analysis with:
- "summary": a well-written multi-paragraph plain-text report describing the overall findings, written for decision-makers.
- "pain_points": list of short phrases (max 12 words each) describing problems users face
- "desired_features": list of short phrases describing improvements or features users want
- "recurring_issues": list of short phrases describing problems that repeat across responses
- "suggested_solutions": list of objects {{"source": "Pain Point" or "Recurring Issue" or "Requested Feature", "issue": "<matching issue text from above>", "solution": "1-2 sentence concrete, actionable solution"}}

Respond with ONLY a JSON object, no extra text, exactly in this format:
{{"summary": "...", "pain_points": ["..."], "desired_features": ["..."], "recurring_issues": ["..."], "suggested_solutions": [{{"source": "Pain Point", "issue": "...", "solution": "..."}}]}}"""
        raw = _ollama_call(prompt, oll_cfg, json_mode=True, max_output_tokens=3072)
        data = ollama_ai.parse_json_block(raw)
        if not isinstance(data, dict):
            return None

        solutions = []
        for item in (data.get("suggested_solutions") or []):
            if not isinstance(item, dict):
                continue
            issue = str(item.get("issue", "")).strip()
            solution = str(item.get("solution", "")).strip()
            source = str(item.get("source", "Pain Point")).strip() or "Pain Point"
            if issue and solution:
                solutions.append({"source": source, "issue": issue, "solution": solution})

        summary_text = str(data.get("summary", "")).strip()
        if not summary_text:
            return None

        def clean_str_list(value):
            if not isinstance(value, list):
                return []
            return [str(item).strip() for item in value if str(item).strip()]

        return {
            "summary": summary_text,
            "pain_points": clean_str_list(data.get("pain_points")),
            "desired_features": clean_str_list(data.get("desired_features")),
            "recurring_issues": clean_str_list(data.get("recurring_issues")),
            "suggested_solutions": solutions,
        }
    except Exception as exc:
        logger.warning("Ollama summary error: %s", exc)
        return None


def _ollama_recommendations(pain_points: list, desired_features: list,
                            recurring_issues: list, user_role: str, oll_cfg: dict):
    """AI-generate prioritized recommendations via a local Ollama model. Returns list of {title, action, priority, based_on} or None."""
    if ollama_ai is None:
        return None
    try:
        findings = (
            [f"Pain point: {p}" for p in (pain_points or []) if p] +
            [f"Requested feature: {f}" for f in (desired_features or []) if f] +
            [f"Recurring issue: {r}" for r in (recurring_issues or []) if r]
        )
        if not findings:
            return None
        numbered = "\n".join(f"- {f}" for f in findings)
        role_line = f"\nThe interviewed stakeholder category is: {user_role}.\n" if user_role else ""
        prompt = f"""You are an IT consultant turning stakeholder interview findings into prioritized recommendations.

Findings:
{numbered}
{role_line}
For each distinct finding (merge duplicates), give ONE actionable recommendation. Each recommendation needs:
- "title": short title of the action (e.g. "Automate the Slow Process")
- "action": 1-2 sentence concrete next step the organization should take
- "priority": exactly one of "High", "Medium", "Low"
- "based_on": the finding text this recommendation came from

Respond with ONLY a JSON array, no extra text, exactly in this format:
[{{"title": "...", "action": "...", "priority": "High", "based_on": "..."}}]"""
        raw = _ollama_call(prompt, oll_cfg, json_mode=True, max_output_tokens=3072)
        data = ollama_ai.parse_json_block(raw)
        if not isinstance(data, list) or not data:
            return None

        recommendations = []
        seen = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            action = str(item.get("action", "")).strip()
            if not title or not action or title.lower() in seen:
                continue
            seen.add(title.lower())
            priority = str(item.get("priority", "Medium")).strip().capitalize()
            if priority not in ("High", "Medium", "Low"):
                priority = "Medium"
            based_on = str(item.get("based_on", "")).strip() or title
            recommendations.append({
                "title": title,
                "action": action,
                "priority": priority,
                "based_on": based_on,
            })
        if not recommendations:
            return None
        order = {"High": 0, "Medium": 1, "Low": 2}
        recommendations.sort(key=lambda rec: order.get(rec["priority"], 3))
        return recommendations
    except Exception as exc:
        logger.warning("Ollama recommendations error: %s", exc)
        return None

