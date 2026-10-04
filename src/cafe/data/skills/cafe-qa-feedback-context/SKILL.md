---
name: cafe-qa-feedback-context
description: Expose a declared QA correction report when development receives acceptance feedback.
version: 1.0.0
workflow:
  prompt_inputs:
    - artifacts: [qa_feedback]
      placeholder: qa_feedback_file
      required: false
---

# QA Feedback Context

## Purpose
- Expose the QA report to the development phase's declared feedback binding.

## Instructions
- When runtime selects QA as the current causal correction source, read the
  report at {qa_feedback_file} for its scenarios, observed failures, and
  reproduction evidence. Execute only the runtime-selected corrective Todo
  items; an available historical QA report does not create another worklist.
