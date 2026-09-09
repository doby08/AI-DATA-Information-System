"""
AI Service Module for Interview System
Handles question generation, response analysis, and report generation

Optional Gemini AI upgrade (see gemini_ai.py): when the user has enabled and
configured a Gemini API key in Settings, the functions below use real AI
generation. If Gemini is disabled, unconfigured, offline, or errors out,
every function transparently falls back to the built-in rule-based logic
so the system keeps working.
"""

import logging

# Optional Gemini upgrade layer (same project folder). The system works
# without it — all functions fall back to the rule-based logic below.
try:
    import gemini_ai
except ImportError:  # pragma: no cover
    gemini_ai = None

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
    
    # Gemini AI upgrade: generate questions with real AI when configured
    if _ai_enabled(gemini_cfg):
        ai_questions = _gemini_questions(system_type, user_role, max_questions, gemini_cfg,
                                         extra_context=extra_context)
        if ai_questions:
            logger.info("Gemini generated %d interview questions (system=%s, role=%s)",
                        len(ai_questions), system_type, user_role)
            return ai_questions
        logger.info("Gemini question generation unavailable; using built-in templates")
    
    # Normalize inputs
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
        
        if max_questions and max_questions > 0:
            return questions[:max_questions]
        return questions
    else:
        # Return generic questions if system type not found
        return get_generic_questions()


def get_generic_questions() -> list:
    """Return generic interview questions for any system."""
    return [
        {
            "text": "What is your primary role with this system?",
            "category": "context"
        },
        {
            "text": "What are the main challenges you face?",
            "category": "pain_points"
        },
        {
            "text": "What features would be most valuable?",
            "category": "desired_features"
        },
        {
            "text": "How much time do you spend on this system daily?",
            "category": "workflows"
        },
        {
            "text": "What improvements would have the most impact?",
            "category": "desired_features"
        }
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
    if _ai_enabled(gemini_cfg) and responses:
        ai_points = _gemini_key_points(responses, gemini_cfg)
        if ai_points:
            return ai_points
        logger.info("Gemini key-point extraction unavailable; using keyword matching")
    
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


def _numbered_texts(responses: list) -> str:
    """Format response texts as a numbered list for prompts."""
    return "\n".join(f"{i + 1}. {str(r).strip()}" for i, r in enumerate(responses) if r and str(r).strip())


def _gemini_questions(system_type: str, user_role: str, max_questions: int, gemini_cfg: dict,
                      extra_context: str = None):
    """AI-generate interview questions. Returns list of {text, category} or None."""
    try:
        count = max_questions if (max_questions and max_questions > 0) else 6
        context_line = ""
        if extra_context and str(extra_context).strip():
            context_line = f"- Interview context: {str(extra_context).strip()}\n"
        prompt = f"""You are an expert systems analyst preparing stakeholder interview questions.

Context:
- System being studied: {str(system_type).replace('_', ' ')}
- Interviewee role: {str(user_role).replace('_', ' ')}
{context_line}
Generate exactly {count} open-ended interview questions about how the interviewee uses this system, the challenges they face, and what improvements they want. Return ONLY {count} questions. Questions must be easy to understand and answerable by a non-technical person.

Each question must have a "category" chosen from exactly one of: personal_info, goals, challenges, preferences, suggestions, pain_points, workflows, desired_features, context.

Respond with ONLY a JSON array, no extra text, in this exact format:
[{{"text": "Question text here?", "category": "pain_points"}}]"""
        raw = gemini_ai.call_gemini(prompt, api_key=gemini_cfg["api_key"], model=gemini_cfg.get("model"))
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
        if max_questions and max_questions > 0:
            questions = questions[:max_questions]
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
