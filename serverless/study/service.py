"""Blinded, immutable AI-pilot sessions. Human enrollment is deliberately closed.

The same service backs Lambda and the local end-to-end pilot. Invitations, not
browser-supplied participant fields, determine respondent provenance.
"""
import base64
import hashlib
import hmac
import json
import time
import uuid

PROFILES = {
    "overlap": "Pay particular attention to objects intruding into each other.",
    "boundaries": "Pay particular attention to furniture crossing room boundaries.",
    "support": "Pay particular attention to apparently floating or sunken objects; state uncertainty when boxes cannot establish support.",
    "clearance": "Pay particular attention to connected space for moving around the room.",
    "access": "Pay particular attention to whether furniture appears reachable and usable.",
    "orientation": "Pay particular attention to the explicitly marked front direction of furniture relative to nearby objects and room space.",
    "proportions": "Pay particular attention to relative oriented bounding-box volumes among the objects present; shape and aspect ratio are outside this question.",
    "relationships": "Pay particular attention to sensible relationships between the kinds of objects shown.",
    "room_function": "Pay particular attention to whether the arrangement serves its stated room type.",
    "overall": "Consider the arrangement as a whole, balancing visible spatial problems rather than one issue alone.",
}
FOCUS_PROFILES = {
    "orientation": (
        "Each object has a cyan arrow marking its source-defined front direction. Compare whether those marked fronts "
        "are oriented sensibly relative to walls, usable room space, and the other objects that are present. "
        "For objects whose shape or function is rotationally symmetric, do not invent a preferred facing direction."
    ),
    "proportions": (
        "Compare which room has more believable relative oriented bounding-box volumes among the objects shown. "
        "Judge the ratios of occupied box volume, not object shape or aspect ratio. The panels are fitted independently, "
        "so absolute canvas size and absolute room scale are outside this question."
    ),
    "relationships": (
        "Compare the actual distances, grouping, and spatial relationships among the objects shown. Judge placement, not whether the inventory contains a conventional pairing."
    ),
    "access": (
        "Compare visible approach space, circulation, and whether the objects shown appear reachable and usable from the available floor area."
    ),
    "room_function": (
        "Given exactly the inventory shown, compare whether its placement organizes those objects into coherent usable zones for the stated room type. "
        "Do not reward a broader or more conventional inventory."
    ),
}
RUBRIC = ("Choose the more plausible indoor arrangement from the evidence provided, or tie if there is no defensible preference. "
          "Use the same overall plausibility criterion regardless of your inspection emphasis. "
          "Mark which side has obvious spatial problems, give confidence 1 (very uncertain) to 5 (very confident), and a short evidence-based rationale. "
          "Do not infer hidden geometry, method identity, or unavailable details. Do not consult other reviewers or numerical benchmark scores.")
FOCUS_ONLY_RUBRIC = (
    "Judge only the assigned dimension. Choose the side that is better on that dimension, or tie when neither side has a defensible advantage. "
    "Do not let overlap, boundary containment, or another unassigned quality determine the choice unless it makes the assigned dimension impossible to inspect. "
    "The object sets are fixed experimental inputs and may differ. Judge only how the objects that are present are arranged. "
    "Do not reward or penalize inventory composition, the presence of a useful object category, conventional pairings, or breadth of function. "
    "An object having no conventional counterpart is not a defect; never infer that either method should have generated another object. "
    "Separately mark which side has the clearer visible problem on the assigned dimension; this is not a technical or rendering-error field and may be both, neither, or uncertain. "
    "Give confidence from 1 (very uncertain) to 5 (very confident) and explain only the visible evidence relevant to the assigned dimension. "
    "Do not infer hidden geometry, method identity, absolute scale between independently fitted panels, or unavailable details. Do not consult other reviewers or numerical benchmark scores."
)
EVIDENCE_RUBRICS = {
    "visual_only": (" Use only the method-blind plan, oblique, and 3D bird's-eye views, including the explicit front-direction arrows. "
                    "Judge visible layout geometry; do not infer mesh detail or compare absolute scale between independently fitted panels."),
    "metrics_only": (" Use only the method-blind per-room measurements. Lower is better for intrusion, boundary, support-gap, and floor-penetration values. "
                     "Connected clearance is contextual rather than universally better. Treat unavailable as unknown, never as zero, and do not infer visual appearance."),
    "combined": (" Use both the method-blind plan, oblique, and 3D bird's-eye views and the per-room measurements. Consider visible functional arrangement and measured spatial validity separately, "
                 "then explain which evidence determined your overall choice and any disagreement between them."),
}


def review_instructions(document, profile=None):
    mode = document.get("evidenceMode", "visual_only")
    if mode not in EVIDENCE_RUBRICS:
        raise ValueError("Unknown study evidence mode")
    if document.get("decisionScope", "overall") == "focus_only":
        if mode != "visual_only":
            raise ValueError("Focused dimension reviews must remain visual-only")
        if profile not in FOCUS_PROFILES:
            raise ValueError("A registered review dimension is required")
        return FOCUS_ONLY_RUBRIC + " Assigned dimension: " + FOCUS_PROFILES[profile] + EVIDENCE_RUBRICS[mode]
    return RUBRIC + EVIDENCE_RUBRICS[mode]


def prompt_text(document, profile):
    """Return every instruction pinned by the immutable prompt hash."""
    instructions = review_instructions(document, profile)
    return instructions if document.get("decisionScope", "overall") == "focus_only" else instructions + "\n" + PROFILES[profile]


class StudyError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class StudyService:
    def __init__(self, document, store, secret, enabled=False, clock=time.time):
        self.document, self.store, self.secret = document, store, secret
        self.enabled, self.clock = enabled, clock

    def signature(self, value):
        return hmac.new(self.secret, value.encode(), hashlib.sha256).hexdigest()

    def invite(self, reviewer_id, profile, model, lifetime=86400):
        allowed_profiles = set(self.document.get("reviewerPlan") or PROFILES)
        if profile not in PROFILES or profile not in allowed_profiles or not reviewer_id or not model:
            raise ValueError("Reviewer ID, known profile and actual model provenance are required")
        claims = {"reviewerId": reviewer_id, "promptProfile": profile, "model": model,
                  "studyVersion": self.document["studyVersion"], "expiresAt": int(self.clock())+lifetime}
        payload = base64.urlsafe_b64encode(packed(claims).encode()).decode().rstrip("=")
        return payload+"."+self.signature("pilot-invitation:"+payload)

    def token(self, session_id):
        return self.signature("pilot-session:"+session_id)

    def start(self, body):
        if not self.enabled or not self.document.get("pilotCollectionEnabled") or not self.document.get("cases"):
            raise StudyError(503, "STUDY_NOT_COLLECTING", "Human enrollment is closed. The AI pilot is not currently accepting sessions.")
        invitation = body.get("invitation", "")
        try:
            payload, signature = invitation.split(".")
            if not hmac.compare_digest(signature, self.signature("pilot-invitation:"+payload)):
                raise ValueError()
            claims = json.loads(base64.urlsafe_b64decode(payload+"="*(-len(payload)%4)))
            if claims["expiresAt"] <= self.clock() or claims["studyVersion"] != self.document["studyVersion"] or claims["promptProfile"] not in PROFILES:
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise StudyError(403, "PILOT_INVITATION_REQUIRED", "A valid AI-pilot invitation is required. Human enrollment remains closed.")
        identity = {key: claims[key] for key in ("reviewerId", "studyVersion")}
        session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, self.signature("pilot-identity:"+packed(identity))))
        session = self.store.get(session_id)
        if session and any(session[key] != claims[key] for key in ("promptProfile", "model")):
            raise StudyError(409, "REVIEWER_PROVENANCE_CHANGED", "An existing reviewer cannot change model or prompt profile.")
        if not session:
            cases = self.document["cases"]
            # Explicitly balance each baseline, rather than calling coin flips balanced.
            sides = {}
            for condition in {case["comparisonCondition"] for case in cases}:
                group = sorted([case for case in cases if case["comparisonCondition"] == condition],
                               key=lambda case: self.signature(session_id+":side:"+case["id"]))
                # Odd-sized extensions cannot balance within one reviewer. A
                # frozen roster alternates the extra side across reviewers;
                # persisted assignments are never regenerated by this policy.
                offset = self.document.get("reviewerSideOffsets", {}).get(claims["reviewerId"], 0) if len(group) % 2 else 0
                if offset not in (0, 1):
                    raise ValueError("Reviewer side offset must be zero or one")
                sides.update({case["id"]: (index + offset) % 2 == 1 for index,case in enumerate(group)})
            assignments = []
            for case in sorted(cases, key=lambda case: self.signature(session_id+":order:"+case["id"])):
                flip = sides[case["id"]]
                images = case.get('profileImages', {}).get(claims['promptProfile'], case)
                assignments.append({"caseId": case["id"], "title": case["title"],
                                    "leftImage": images["comparisonImage"] if flip else images["relationImage"],
                                    "rightImage": images["relationImage"] if flip else images["comparisonImage"],
                                    "leftMetrics": case.get("comparisonMetrics", []) if flip else case.get("relationMetrics", []),
                                    "rightMetrics": case.get("relationMetrics", []) if flip else case.get("comparisonMetrics", []),
                                    "leftCondition": case["comparisonCondition"] if flip else "soilie",
                                    "rightCondition": "soilie" if flip else case["comparisonCondition"],
                                    "comparisonCondition": case["comparisonCondition"], "repeatOf": None})
            # Two consistency trials are held out of preference totals. Their
            # control status is private until all reviewer responses are frozen.
            for index, original in enumerate(assignments[:2]):
                repeat = dict(original, caseId=self.signature(session_id+f":repeat:{index}")[:20], repeatOf=original["caseId"])
                repeat["leftImage"], repeat["rightImage"] = original["rightImage"], original["leftImage"]
                repeat["leftMetrics"], repeat["rightMetrics"] = original["rightMetrics"], original["leftMetrics"]
                repeat["leftCondition"], repeat["rightCondition"] = original["rightCondition"], original["leftCondition"]
                assignments.append(repeat)
            session = {"sessionId": session_id, "respondentType": "ai_pilot", **claims,
                       "evidenceMode": self.document.get("evidenceMode", "visual_only"),
                       "expiresAt": int(self.clock())+7*86400, "createdAt": int(self.clock()),
                       "decisionScope":self.document.get("decisionScope", "overall"),
                       "promptHash": hashlib.sha256(prompt_text(self.document, claims["promptProfile"]).encode()).hexdigest(),
                       "assignments": assignments}
            self.store.create(session_id, session)
            session = self.store.get(session_id)
        return {**self.public_session(session), "sessionToken": self.token(session_id)}

    def public_session(self, session):
        # An assignment can outlive a deployment. Never silently change the
        # instructions under which an existing reviewer is completing it.
        pinned_document = {"evidenceMode":session.get("evidenceMode", "visual_only"),
                           "decisionScope":session.get("decisionScope", "overall")}
        instructions = review_instructions(pinned_document, session["promptProfile"])
        prompt_hash = hashlib.sha256(prompt_text(pinned_document, session["promptProfile"]).encode()).hexdigest()
        if not hmac.compare_digest(session["promptHash"], prompt_hash):
            raise StudyError(409, "STUDY_PROTOCOL_CHANGED", "This session's original review instructions are no longer available. Contact the study organizer.")
        mode = session.get("evidenceMode", "visual_only")
        public_fields = ["caseId", "title"]
        if mode in {"visual_only", "combined"}:
            public_fields.extend(("leftImage", "rightImage"))
        if mode in {"metrics_only", "combined"}:
            public_fields.extend(("leftMetrics", "rightMetrics"))
        return {"sessionId": session["sessionId"], "studyVersion": session["studyVersion"], "respondentType": "ai_pilot",
                "evidenceMode":mode,"decisionScope":session.get("decisionScope", "overall"),
                "rubric": instructions, "emphasis": PROFILES[session["promptProfile"]],
                "decisionQuestion":("Which arrangement is better on the assigned dimension?"
                                    if session.get("decisionScope") == "focus_only" else "Which arrangement looks more spatially plausible?"),
                "problemQuestion":("Which side has the clearer problem on the assigned dimension?"
                                   if session.get("decisionScope") == "focus_only" else "Which side has obvious spatial problems?"),
                "cases": [{key: case[key] for key in public_fields} for case in session["assignments"]],
                "completedCaseIds": [row["caseId"] for row in self.store.responses(session["sessionId"])]}

    def authorized(self, session_id, body):
        if not self.enabled:
            raise StudyError(503, "STUDY_NOT_COLLECTING", "AI-pilot collection is paused.")
        token = body.get("sessionToken")
        if not isinstance(token, str) or not token.isascii() or not hmac.compare_digest(token, self.token(session_id)):
            raise StudyError(403, "STUDY_SESSION_FORBIDDEN", "This session token is not valid.")
        session = self.store.get(session_id)
        if not session or session["expiresAt"] <= self.clock():
            raise StudyError(404, "STUDY_SESSION_NOT_FOUND", "The session was not found or has expired.")
        if session["respondentType"] != "ai_pilot":
            raise StudyError(403, "HUMAN_ENROLLMENT_CLOSED", "Human enrollment remains closed.")
        return session

    def resume(self, session_id, body):
        return self.public_session(self.authorized(session_id, body))

    def respond(self, session_id, body):
        session = self.authorized(session_id, body)
        self.public_session(session) # Enforce the same immutable prompt on resume and submission.
        case = next((case for case in session["assignments"] if case["caseId"] == body.get("caseId")), None)
        confidence = body.get("confidence")
        note = body.get("note", "")
        # Tuples also reject malformed JSON arrays/objects without triggering an
        # unhashable-type error before the API can return a validation response.
        if (case is None or body.get("judgement") not in ("left","tie","right")
            or body.get("errorChoice") not in ("left","right","both","neither","uncertain")
            or type(confidence) is not int or not 1 <= confidence <= 5 or not isinstance(note,str) or len(note)>500):
            raise StudyError(400, "INVALID_STUDY_RESPONSE", "Choose both judgements, confidence from 1 to 5, and a rationale of at most 500 characters.")
        row = {key: body[key] for key in ("caseId","judgement","errorChoice","confidence")}
        row.update({"note":note.strip(), "respondentType":"ai_pilot", "reviewerId":session["reviewerId"],
                    "recordedAt":int(self.clock()),
                    "promptProfile":session["promptProfile"], "model":session["model"], "promptHash":session["promptHash"],
                    "studyVersion":session["studyVersion"], "evidenceMode":session.get("evidenceMode", "visual_only"), "leftCondition":case["leftCondition"],
                    "rightCondition":case["rightCondition"], "comparisonCondition":case["comparisonCondition"],
                    "repeatOf":case["repeatOf"]})
        saved = self.store.save_response(session_id, row)
        if {key:value for key,value in saved.items() if key != "recordedAt"} != {key:value for key,value in row.items() if key != "recordedAt"}:
            raise StudyError(409, "RESPONSE_ALREADY_SAVED", "A different response is already saved for this case.")
        return {"saved":True,"caseId":case["caseId"]}
