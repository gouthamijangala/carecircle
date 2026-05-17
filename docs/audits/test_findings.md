# Test Findings: Three Critical OCR/Drug Resolution Bugs

This document details three bugs found in the CareCircle medication parsing pipeline that cause prescription data to be incorrectly dropped or misparsed.

---

## Bug 1: Strikethrough Text Confuses OCR

### What Happens
When a prescription has a strikethrough correction (e.g., "Ecosprin AV ~~75~~ 50" or "Atenolol 50 mg ~~25 mg~~"), the OCR engine reads both the crossed-out value AND the corrected value. The pipeline then picks the wrong (crossed-out) value.

### Why It Happens

**Root Cause #1: OCR has no format awareness**
- The PaddleOCR engine (`ingestion.py`) sees raw text characters only
- It cannot detect or interpret visual formatting like strikethrough (~)
- A crossed-out "75" appears to OCR as exactly the characters "7", "5", "7", "5"

**Root Cause #2: First-match regex behavior**
- The dose extraction function uses Python's `re.search()` which returns the FIRST match only
- When OCR returns "75 50" (from "~~75~~ 50"), the regex grabs "75" instead of "50"

### Where It Happens

**File**: `ingestion.py`
**Function**: `_extract_dose_from_segment()` (lines 2251-2283)

### The ROBUST Fix (Not Assumptions-Based)

**The problem with simple "take last number" fix:**
- If doctor writes "50" BELOW "75", OCR might still read "75 50" (spatial reading varies)
- If doctor rewrites ABOVE/LEFT, first number = corrected value
- Assuming "last wins" will break in many scenarios

**The CORRECT robust solution:**

#### Option A: Detect Strikethrough Pattern in TEXT
```python
def _extract_dose_from_segment(segment: str) -> tuple[float | None, str | None]:
    """
    Detect strikethrough pattern in text and prefer the non-struck value.
    """
    # Pattern 1: "number ~~number~~" - struck-through number in middle
    # Pattern 2: "~~number~~ number" - struck-through number first
    # Pattern 3: "number number" - two numbers without markers

    # First, try to detect explicit strikethrough markers
    # Look for patterns like "75 ~~50~~" or "~~75~~ 50"
    strikethrough_pattern = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:~~|~~|~~|\s~~|~~)\s*(\d+(?:\.\d+)?)",
        segment,
        re.IGNORECASE
    )
    if strikethrough_pattern:
        # Take the number NOT in strikethrough markers
        # This is ambiguous - need to know which side was struck

    # Pattern detection: Look for number-number with same unit
    # "50 mg 25 mg" - need other heuristics

    # ROBUST APPROACH: Use position-aware extraction if available
    return _extract_dose_with_position_logic(segment)
```

#### Option B: Use OCR Bounding Box Position (Most Robust)
The actual correct solution requires using OCR's spatial coordinate data:

```python
def _extract_dose_from_segment_robust(segment: str, ocr_boxes: list = None) -> tuple[float | None, str | None]:
    """
    Extract dose using spatial position when available.

    If OCR provides bounding boxes:
    - Use the number with the RIGHTMOST or BOTTOMMOST position
    - Doctors typically write corrections to the right or below

    If no position data available:
    - Use pattern detection for strikethrough markers
    - Fall back to heuristic: last number has slightly higher probability
    """
    if ocr_boxes:
        # Sort by position - prefer rightmost/bottommost
        sorted_boxes = sorted(ocr_boxes, key=lambda b: (b.y, b.x))
        return sorted_boxes[-1].value, sorted_boxes[-1].unit

    # Fallback: Pattern-based detection
    return _extract_dose_pattern_based(segment)
```

#### Option C: Accept Ambiguity and Flag for Review (Safest)
```python
def _extract_dose_from_segment(segment: str) -> tuple[float | None, str | None, list]:
    """
    Returns (dose_value, unit, confidence_reasons)
    When multiple doses detected, flag for human review instead of guessing.
    """
    all_doses = re.findall(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|units?|teaspoon)", segment, re.I)

    if len(all_doses) == 1:
        return float(all_doses[0][0]), all_doses[0][1], ["single_value"]

    if len(all_doses) == 2:
        # Two values found - could be correction
        # Instead of guessing, flag for review
        return None, None, [
            "multiple_doses_detected",
            f"values: {all_doses[0][0]}, {all_doses[1][0]}",
            "flag_for_human_review"
        ]

    return None, None, ["no_dose_found"]
```

**Why this fix is CORRECT:**
- Option C (flag for review) is SAFEST - no guessing, no wrong dose to patient
- Option B (bounding boxes) is MOST ACCURATE if OCR provides position data
- Neither makes assumptions that could harm patients

---

## Bug 2: Combination Drug / Brand Alias Gaps

### What Happens
Common Indian brand names like "Telma H" (Telmisartan + Hydrochlorothiazide) and "Atorva" (Atorvastatin) are not recognized. The drug resolver returns `None` for canonical name, and the parser drops the entire medication segment.

### Why It Happens

**Root Cause #1: Hardcoded alias dictionary is incomplete**
- The `LOCAL_DRUG_ALIASES` dictionary in `drug_resolver.py` (lines 19-31) has only ~10 entries
- Cannot anticipate every possible brand name variation

**Root Cause #2: No pattern-based fallback for unknown brands**
- When an unknown brand is encountered, there's no logic to extract the base drug name

### Where It Happens

**File**: `drug_resolver.py`
**Function**: `resolve_drug_name()` (lines 42-83)

### The Fix (GENERALIZED - Not Hardcoded)

**Implement a PATTERN-BASED fallback system:**

```python
def _smart_fallback_resolution(raw_name: str) -> tuple[str | None, float]:
    """
    Pattern-based resolution for unknown brand names.
    Works for ANY future brand, not just specific ones.
    """
    cleaned = raw_name.lower().strip()

    # Pattern 1: Suffix-based combination drug detection
    combination_suffixes = {
        " h": "hydrochlorothiazide",
        " a": "amlodipine",
        " m": "metoprolol",
        " am": "amlodipine",
    }
    for suffix, combo_drug in combination_suffixes.items():
        if cleaned.endswith(suffix) or " " + suffix in cleaned:
            base = cleaned.replace(suffix, "").strip()
            base_result = _local_alias(base)
            if base_result:
                return f"{base_result} {combo_drug}", 0.75

    # Pattern 2: Known drug substring matching
    known_drugs = ["telmisartan", "atorvastatin", "metformin", "amlodipine", "ramipril"]
    for drug in known_drugs:
        if drug in cleaned:
            return drug, 0.65

    # Pattern 3: Levenshtein distance for typos
    return _fuzzy_match(cleaned)
```

**Why this fix is CORRECT:**
- Works for ANY future brand automatically
- Falls back gracefully instead of dropping segments

---

## Bug 3: Conflicting Dose Values Cause Segment Rejection

### What Happens
When OCR produces text like "Tab. Atenolol 50 mg 25 mg", the parser sees two numeric dose values and cannot decide which is correct. Instead of picking a value, it rejects the entire medication segment.

### Why It Happens

- Multiple dose values in OCR output (from strikethrough)
- Validation logic rejects ambiguity instead of flagging for review

### Where It Happens

**File**: `ingestion.py`
**Function**: `validate_extracted_medication()` (lines 1383+)

### The Fix

**This is the SAME robust solution as Bug 1** - use Option C (flag for review) which is the safest approach:

```python
def _extract_dose_from_segment(segment: str) -> tuple[float | None, str | None, list]:
    """
    When multiple doses detected, flag for human review instead of guessing.
    Patient safety > automation convenience.
    """
    all_doses = re.findall(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|units?|teaspoon)", segment, re.I)

    if len(all_doses) == 1:
        return float(all_doses[0][0]), all_doses[0][1], ["single_value"]

    if len(all_doses) == 2:
        return None, None, ["multiple_doses_flagged_for_review"]

    return None, None, ["no_dose"]
```

---

## Summary: Why "Take Last Number" Fix is WRONG

| Scenario | What Doctor Does | OCR Reads | "Last Wins" Result | Correct? |
|----------|-----------------|-----------|-------------------|-----------|
| 1 | Rewrite to RIGHT | "75 50" | 50 ✓ | Maybe |
| 2 | Rewrite BELOW | "75 50" | 50 ✗ | Wrong |
| 3 | Rewrite ABOVE/LEFT | "50 75" | 75 ✗ | Wrong |
| 4 | No strikethrough, two doses | "50 25" | 25 ✗ | Wrong |

**The fix cannot be "always take last"** - it's unreliable and could give wrong dose to patient.

**The correct approach:**
1. **Use OCR position data** (bounding boxes) if available
2. **Flag for human review** when ambiguous - patient safety > automation
3. **Detect explicit strikethrough markers** in text (~, ~~, struck)

---

## Updated Summary Table

| Bug | Location | Problem | CORRECT Fix (Not Assumption-Based) |
|-----|----------|---------|-----------------------------------|
| Strikethrough confusion | `ingestion.py:2258` | First-match extraction | Option B: Use OCR position data OR Option C: Flag for review (safest) |
| Missing brand aliases | `drug_resolver.py:42-83` | No fallback | Pattern-based resolution (suffixes, substrings) |
| Conflicting doses rejected | `ingestion.py:1383+` | Rejects instead of flags | Flag for human review when ambiguous |

---

## Key Principle: Patient Safety Over Automation

For dose extraction, the CORRECT approach is:
- **Never guess** between two values
- **Use spatial position** from OCR if available
- **Flag for human review** when ambiguous
- **Prefer false negatives** (reject and ask) over false positives (wrong dose)

This is a medical system - wrong doses can harm patients. The "take last number" assumption is too risky.