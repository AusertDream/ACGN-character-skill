# ACGN Video Dialogue Extraction Pipeline

## Goal Description

Build an OCR-first dialogue extraction tool for ACGN/visual novel style videos to replace the current ASR-based approach. The tool will extract high-quality dialogue text from videos where characters speak in dialogue boxes, handling cases with no voice acting, background music interference, and on-screen text as the authoritative source. The extracted dialogue will be structured as speaker-dialogue pairs in a format compatible with the existing yuexia-skill character generation pipeline (story_analyzer.md and persona_analyzer.md prompts).

## Acceptance Criteria

Following TDD philosophy, each criterion includes positive and negative tests for deterministic verification.

- AC-1: Dialogue Event Extraction Completeness
  - Positive Tests (expected to PASS):
    - On a manually labeled 10-20 minute test clip with standard dialogue UI, the system extracts at least 90% of dialogue events (recall ≥ 90%)
    - Each extracted event includes video source, timestamp range, speaker field, text field, and confidence score
    - Events with visible name boxes have speaker attribution
  - Negative Tests (expected to FAIL):
    - Non-dialogue scenes (battle HUD, menus, loading screens) should NOT generate dialogue events
    - Duplicate events from the same dialogue line should be rejected or merged
    - Events with empty text fields should be flagged for review or rejected

- AC-2: Text Quality and Deduplication
  - Positive Tests (expected to PASS):
    - Character Error Rate (CER) on standard dialogue scenes is below a user-agreed threshold when compared to manual transcription
    - Typewriter effect (progressive text display) results in one complete sentence, not multiple partial sentences
    - Long dialogue lines that span multiple frames are captured as single complete events
  - Negative Tests (expected to FAIL):
    - Partial sentences from typewriter animation should NOT appear as separate events in final output
    - The same dialogue line repeated across consecutive frames should NOT create duplicate events (duplicate rate < 10%)
    - Garbled OCR output with confidence below threshold should be flagged for review, not accepted as final

- AC-3: Output Format Compatibility
  - Positive Tests (expected to PASS):
    - The tool produces plain text output in `[HH:MM:SS] Speaker: Dialogue` format compatible with existing story_analyzer.md and persona_analyzer.md prompts
    - The tool produces structured JSONL output with fields: video_id, event_id, start_ms, end_ms, speaker, text, confidence, review_required
    - Output files can be placed in `training data/transcripts/` and consumed by existing character generation workflow without modification
  - Negative Tests (expected to FAIL):
    - Output with missing speaker or text fields should be rejected or flagged
    - Output with malformed timestamps should be rejected
    - Output that cannot be parsed by existing prompt templates should be rejected

- AC-4: ROI Configuration and Calibration
  - Positive Tests (expected to PASS):
    - The system supports per-work ROI configuration files (YAML/JSON) defining name box and dialogue box regions
    - ROI calibration can be performed on sample frames and validated before full video processing
    - ROI coordinates are resolution-independent or support resolution normalization
  - Negative Tests (expected to FAIL):
    - Processing without ROI configuration should fail with clear error message
    - Invalid ROI coordinates (negative, out of bounds, zero area) should be rejected
    - ROI configuration for wrong resolution should be detected and rejected or auto-scaled

- AC-5: Batch Processing and Resumability
  - Positive Tests (expected to PASS):
    - The tool can process multi-hour videos without manual intervention
    - Processing can be interrupted and resumed from the last completed event
    - Progress is logged and intermediate results are saved
  - Negative Tests (expected to FAIL):
    - Processing should NOT restart from beginning if interrupted
    - Processing should NOT silently skip videos or segments without logging
    - Processing should NOT continue if critical errors occur (missing ROI config, OCR engine failure)

- AC-6: Review and Provenance
  - Positive Tests (expected to PASS):
    - Low-confidence events (below threshold) are flagged with review_required=true
    - Each event can be traced back to source video file, timestamp, and frame/crop images
    - Review artifacts (keyframes, ROI crops, OCR candidates) are saved for manual inspection
  - Negative Tests (expected to FAIL):
    - Events without provenance information should be rejected
    - High-confidence events should NOT be flagged for review unnecessarily
    - Review artifacts should NOT be missing when review_required=true

## Path Boundaries

Path boundaries define the acceptable range of implementation quality and choices.

### Upper Bound (Maximum Acceptable Scope)

The implementation includes a complete four-layer pipeline with dialogue segment detection, ROI-based OCR with multi-frame fusion, event aggregation with typewriter handling, speaker attribution with character name normalization, structured JSONL output with provenance, plain text export compatible with existing prompts, batch processing with resume support, review UI for low-confidence events, semi-transparent background preprocessing profiles, two-stage OCR (fast scan + precise scan), and optional ASR/VLM fallback for difficult cases. The system supports per-work configuration files, resolution normalization, multiple preprocessing profiles, confidence-based review routing, and integration with the existing yuexia-skill character generation workflow.

### Lower Bound (Minimum Acceptable Scope)

The implementation includes manual ROI definition for one target work, single-pass OCR on dialogue box and name box regions, basic event detection from text appearance/change/disappearance, typewriter effect merging using prefix-growth detection, speaker extraction from name box OCR, plain text output in `[HH:MM:SS] Speaker: Dialogue` format compatible with existing prompts, and structured JSONL output with basic provenance (video file, timestamp, confidence). The system can process one full video end-to-end with manual review of flagged events.

### Allowed Choices

**Can use:**
- OCR engines: PaddleOCR, EasyOCR, Tesseract, RapidOCR, or cloud OCR APIs (user's choice based on testing)
- Video decoding: ffmpeg + PyAV, OpenCV, or Decord
- Image preprocessing: OpenCV, PIL/Pillow, scikit-image
- Configuration format: YAML or JSON for ROI and work profiles
- Output formats: Plain text (required), JSONL (required), SRT (optional)
- Programming language: Python (to match existing video_transcriber.py)
- Optional fallback: Local VLM, cloud multimodal APIs (if user configures and approves)
- Optional ASR: Whisper for voiced dialogue validation (if user wants to keep it)

**Cannot use:**
- Real-time processing requirements (batch processing is acceptable)
- Hook-based text extraction (videos only, no game process access)
- Approaches that require original game files or emulator access
- Solutions that send copyrighted game frames to external APIs without explicit user approval
- Approaches that cannot integrate with existing `training data/transcripts/*.txt` workflow

## Feasibility Hints and Suggestions

> **Note**: This section is for reference and understanding only. These are conceptual suggestions, not prescriptive requirements.

### Conceptual Approach

**Phase 1: Build Gold Standard First**
Before building the full pipeline, create a manually labeled 10-20 minute test clip with ground truth dialogue events, speakers, and text. This becomes the benchmark for all subsequent development and prevents building the wrong thing.

**Phase 2: ROI Calibration Tool**
Build a simple tool to display video frames, let the user draw rectangles for name box and dialogue box regions, and save coordinates to a YAML/JSON config file. Validate the ROI by showing crops from multiple frames.

**Phase 3: Single-Engine OCR Event Detector**
Implement a state machine that monitors ROI regions frame-by-frame:
- State: IDLE → TEXT_DETECTED → TEXT_GROWING → TEXT_STABLE → FINALIZED → IDLE
- On each frame: crop ROI, run OCR, compare with previous frame
- If text appears: transition to TEXT_DETECTED
- If text grows (prefix match): transition to TEXT_GROWING, accumulate
- If text stable for N frames: transition to TEXT_STABLE, finalize event
- If text disappears: transition to IDLE

**Phase 4: Output Adapter**
Convert event JSONL to plain text format matching existing transcripts:
```
[HH:MM:SS] Speaker: Dialogue text
```
This ensures compatibility with story_analyzer.md and persona_analyzer.md prompts.

**Phase 5: Iterative Enhancement**
After validating the basic pipeline on the gold standard:
- Add preprocessing profiles for semi-transparent backgrounds
- Add second OCR engine for low-confidence events
- Add speaker normalization dictionary
- Add review UI for flagged events

### Relevant References

Existing codebase:
- `yuexia-skill/tools/video_transcriber.py`: Current Whisper-based extraction, shows ffmpeg integration, model loading, batch processing pattern, output format
- `yuexia-skill/prompts/story_analyzer.md`: Expects timestamped text with speaker attribution, shows required output format
- `yuexia-skill/prompts/persona_analyzer.md`: Expects dialogue with speaker labels and original quotes
- `training data/transcripts/*.txt`: Current output format is `[HH:MM:SS] Text` without speaker labels

Referenced open source projects (from draft):
- GameSentenceMiner: Two-stage OCR approach (fast scan for changes, precise scan for finalization)
- Visual Novel OCR: Semi-transparent background handling with color contrast thresholds
- RSTGameTranslation: Context-aware character name recognition and VN chat window optimization
- VideOCR + VideoSubFinder: Long video batch processing and keyframe extraction patterns
- visual-novel-game-ocr: Change-based keyframe filtering to avoid processing every frame

## Dependencies and Sequence

### Milestone 1: Foundation and Validation
Establish the baseline for all subsequent work by creating evaluation infrastructure and proving OCR viability.

- Phase A: Create gold standard dataset
  - Manually label 10-20 minute test clip with dialogue events, speakers, and text
  - Document UI layout characteristics (name box position, dialogue box position, text style)
  - Save representative frames showing different dialogue states (empty, typewriter, complete, transition)

- Phase B: ROI calibration tool
  - Build interactive tool to define and validate ROI regions on sample frames
  - Save ROI configuration to YAML/JSON file
  - Validate ROI by displaying crops from multiple frames across the video

- Phase C: OCR engine evaluation
  - Test 2-3 candidate OCR engines on real dialogue box crops from the gold standard
  - Measure CER, confidence calibration, speed, and GPU memory usage
  - Select primary OCR engine based on test results

### Milestone 2: Core Pipeline (MVP)
Build the minimum viable extraction pipeline that can process one full video end-to-end.

- Phase A: Video processing infrastructure
  - Video decoding and frame extraction at configurable FPS
  - ROI cropping from frames based on configuration
  - Frame-to-frame change detection in ROI regions

- Phase B: Event detection state machine
  - Implement IDLE → TEXT_DETECTED → TEXT_GROWING → TEXT_STABLE → FINALIZED state transitions
  - OCR integration with confidence tracking
  - Typewriter effect detection using prefix-growth matching
  - Event finalization when text stabilizes for N frames

- Phase C: Speaker attribution and output
  - Extract speaker from name box ROI using OCR
  - Generate structured JSONL with provenance (video, timestamp, confidence)
  - Generate plain text output compatible with existing transcript format
  - Implement batch processing with resume support

### Milestone 3: Quality Enhancement
Improve extraction quality on difficult cases and reduce manual review burden.

- Phase A: Preprocessing profiles
  - Implement image preprocessing for semi-transparent backgrounds
  - Add contrast enhancement, binarization, and noise reduction
  - Create per-work preprocessing profiles

- Phase B: Multi-engine fusion and fallback
  - Add second OCR engine for low-confidence events
  - Implement candidate voting or selection logic
  - Optional: Add VLM/API fallback for events below confidence threshold (if user approves)

- Phase C: Speaker normalization
  - Build character name dictionary with aliases
  - Implement speaker inheritance for empty name boxes
  - Add special speaker tags (narrator, system, unknown)

### Milestone 4: Production Readiness
Make the tool robust, maintainable, and easy to use for multiple works.

- Phase A: Review and correction workflow
  - Build review UI showing flagged events with frame/crop provenance
  - Support manual correction and re-export
  - Track correction patterns for future improvement

- Phase B: Multi-work support
  - Generalize configuration system for multiple works
  - Add configuration validation and error reporting
  - Document onboarding process for new works

- Phase C: Integration and testing
  - Integrate with existing yuexia-skill workflow
  - Run regression tests on gold standard
  - Validate output quality with story_analyzer and persona_analyzer prompts

**Dependency Notes:**
- Milestone 2 depends on Milestone 1 (cannot build pipeline without validated ROI and OCR engine)
- Milestone 3 depends on Milestone 2 (cannot enhance quality without working baseline)
- Milestone 4 depends on Milestone 3 (production features require stable core)
- Within each milestone, phases are sequential (A → B → C)

## Task Breakdown

Each task must include exactly one routing tag:
- `coding`: implemented by Claude
- `analyze`: executed via Codex (`/humanize:ask-codex`)

| Task ID | Description | Target AC | Tag | Depends On |
|---------|-------------|-----------|-----|------------|
| task1 | Create gold standard dataset: manually label 10-20 min test clip with dialogue events, speakers, text, and UI characteristics | AC-1, AC-6 | coding | - |
| task2 | Build ROI calibration tool: interactive frame viewer to define name box and dialogue box regions, save to YAML/JSON config | AC-4 | coding | task1 |
| task3 | Evaluate OCR engines: test 2-3 candidates on real crops, measure CER/confidence/speed, select primary engine | AC-2 | analyze | task1 |
| task4 | Implement video processing infrastructure: frame extraction, ROI cropping, change detection | AC-5 | coding | task2, task3 |
| task5 | Implement event detection state machine: IDLE→DETECTED→GROWING→STABLE→FINALIZED transitions with OCR integration | AC-1, AC-2 | coding | task4 |
| task6 | Implement typewriter effect handling: prefix-growth detection and partial sentence merging | AC-2 | coding | task5 |
| task7 | Implement speaker attribution: extract from name box OCR, handle empty cases | AC-1 | coding | task5 |
| task8 | Implement structured JSONL output with provenance: video_id, event_id, timestamps, speaker, text, confidence, review_required | AC-3, AC-6 | coding | task6, task7 |
| task9 | Implement plain text output adapter: convert JSONL to [HH:MM:SS] Speaker: Dialogue format compatible with existing prompts | AC-3 | coding | task8 |
| task10 | Implement batch processing with resume support: process multiple videos, save checkpoints, handle interruptions | AC-5 | coding | task9 |
| task11 | Validate MVP on gold standard: measure recall, CER, duplicate rate against acceptance criteria thresholds | AC-1, AC-2 | analyze | task10 |
| task12 | Implement preprocessing profiles: semi-transparent background handling, contrast enhancement, binarization | AC-2 | coding | task11 |
| task13 | Implement multi-engine OCR fusion: add second engine for low-confidence events, candidate voting logic | AC-2 | coding | task11 |
| task14 | Implement speaker normalization: character name dictionary with aliases, inheritance for empty name boxes, special tags | AC-1 | coding | task11 |
| task15 | Build review UI: display flagged events with frame/crop provenance, support manual correction and re-export | AC-6 | coding | task11 |
| task16 | Implement multi-work configuration system: generalize config structure, add validation, document onboarding | AC-4 | coding | task14 |
| task17 | Integration testing: run full pipeline on target videos, validate output with story_analyzer and persona_analyzer prompts | AC-3 | analyze | task15, task16 |
| task18 | Regression testing: validate quality metrics on gold standard after all enhancements | AC-1, AC-2 | analyze | task17 |

## Claude-Codex Deliberation

### Agreements

Both Claude and Codex agree on the following core points:

- **OCR-first is the correct approach**: For videos with on-screen dialogue boxes, OCR is more reliable than ASR, especially for unvoiced dialogue and when background music/sound effects interfere with speech recognition.

- **Event-based modeling is essential**: The system must model "dialogue events" (complete sentences from appearance to disappearance) rather than processing individual frames, to handle typewriter effects and avoid duplicate/partial sentences.

- **Gold standard dataset is critical**: Building a manually labeled test clip before implementing the full pipeline is the highest-priority de-risking activity. Without ground truth, there's no way to validate whether the approach actually improves extraction quality.

- **ROI configuration is necessary**: Fixed region-of-interest definitions per work dramatically improve OCR quality compared to whole-frame processing, and are worth the manual setup cost.

- **Integration with existing workflow is required**: The output must be compatible with existing story_analyzer.md and persona_analyzer.md prompts, which expect timestamped dialogue with speaker attribution.

- **Batch processing and resumability are important**: Multi-hour videos require checkpoint/resume support to handle interruptions and avoid wasting computation.

- **Review workflow is essential, not optional**: Low-confidence events need human review with provenance (frames, crops, candidates) to maintain quality. This should be built early, not deferred to late stages.

### Resolved Disagreements

- **Timeline and Phase Prioritization**
  - Draft position: P0 (2 weeks) → P1 (3-5 weeks) → P2 (5-8 weeks) → P3 (ongoing)
  - Codex position: Timeline is optimistic; should reorder to build evaluation and review tools earlier
  - Resolution: Adopt Codex's reordering. Milestone 1 now includes gold standard creation and OCR engine evaluation before any pipeline implementation. Review UI moved from P3 to Milestone 3 (after MVP but before multi-work generalization). This reduces risk of building the wrong thing and makes debugging easier.
  - Rationale: Building infrastructure before validation is a common failure mode. The gold standard and review tools are force multipliers for all subsequent work.

- **ASR Role in the Pipeline**
  - Draft position: Section 2.9 says "ASR 辅助模块（不选，放弃这条路）" but earlier sections mention ASR as fallback
  - Codex position: Inconsistent; needs clean decision
  - Resolution: ASR is optional and user-configurable. The core pipeline is OCR-only. If the user wants to keep ASR, it can be used for timestamp hints (identifying voiced spans to prioritize) or as a validation signal for voiced dialogue, but never as the primary text source.
  - Rationale: The draft correctly identifies that ASR is unreliable for this use case, but completely removing it wastes the existing Whisper infrastructure. Making it optional preserves flexibility without adding complexity to the core path.

- **Multi-Engine OCR Strategy**
  - Draft position: Use two OCR engines from the start (OCR1 fast, OCR2 precise)
  - Codex position: Start with one engine, add second only after measuring failure patterns
  - Resolution: Adopt Codex's approach. Task 3 evaluates multiple engines and selects one primary engine. Task 13 (Milestone 3) adds a second engine for low-confidence events only after MVP validation reveals where single-engine fails.
  - Rationale: Multi-engine fusion adds complexity (candidate voting, confidence calibration, latency). Without empirical data on where single-engine fails, it's premature optimization. The two-stage OCR concept (fast scan for changes, precise scan for finalization) is still valuable but can be implemented with one engine using different preprocessing or parameters.

- **VLM/API Fallback Scope**
  - Draft position: VLM/API fallback for low-confidence events is part of P2 (enhanced version)
  - Codex position: Cost, latency, privacy, and copyright concerns make this less practical than it sounds; human review may be better
  - Resolution: VLM/API fallback is optional and requires explicit user approval. It's not part of the core acceptance criteria. If implemented, it must respect privacy/copyright constraints (no external API calls without user consent) and cost limits (capped percentage of events).
  - Rationale: Sending copyrighted game frames to external APIs has legal and privacy implications. For a personal character-building project, human review with a good UI is likely more practical than automated fallback to expensive APIs.

- **Scene Classification Complexity**
  - Draft position: Build dialogue segment detection with multiple layers (rules, lightweight classifier, multi-class classifier)
  - Codex position: Skip scene classification for P0/MVP; manually define ROIs and detect events only from ROI text changes
  - Resolution: Adopt Codex's simplification for MVP. Milestone 2 (MVP) uses ROI-based event detection without scene classification. If needed, scene classification can be added in Milestone 3 as a preprocessing step, but it's not required to satisfy acceptance criteria.
  - Rationale: Scene classification is a complex ML problem that may not be necessary if ROI-based detection works well. The draft's assumption that "dialogue segment detection is higher-risk than the draft treats it" is correct, so deferring it until proven necessary reduces risk.

### Convergence Status

- Final Status: `converged`

All major technical disagreements have been resolved through the milestone restructuring and clarification of optional vs. required components. The plan now has clear acceptance criteria, a de-risked implementation sequence (gold standard first, MVP second, enhancements third), and explicit user decision points for optional features.

## Pending User Decisions

- DEC-1: Near-term goal scope
  - Claude Position: The plan should optimize for improving Yuexia character quality specifically, with multi-work support as a later generalization
  - Codex Position: Need explicit clarification whether the goal is to improve one character or build a reusable ACGN extraction platform
  - Tradeoff Summary: Single-character optimization allows faster iteration and tighter integration with existing yuexia-skill workflow. Platform approach requires more upfront design for configurability and generalization. The milestone structure supports both paths (MVP focuses on one work, Milestone 4 adds multi-work support), but the priority and acceptance criteria should reflect the user's actual goal.
  - Decision Status: **Build reusable ACGN platform** - The system should be designed with multi-work support in mind from the start, with proper configuration abstraction and generalization. However, initial validation will focus on Yuexia videos.

- DEC-2: Source video homogeneity
  - Claude Position: Assume source videos for a given work have consistent UI layout, resolution, and subtitle style (as stated in draft)
  - Codex Position: Need validation that this assumption holds for actual source videos (Bilibili uploads may have black bars, fan subtitles, variable resolution)
  - Tradeoff Summary: If videos are homogeneous, manual ROI per work is practical. If videos vary significantly, the system needs resolution normalization, letterbox detection, and possibly per-video ROI adjustment. This affects Milestone 1 (ROI calibration) and Milestone 2 (video processing infrastructure) complexity.
  - Decision Status: **Mostly consistent with minor variations** - Videos have same UI but may have black bars, slight resolution differences, or occasional layout changes. The system must include resolution normalization and letterbox detection in Milestone 2.

- DEC-3: Language support scope
  - Claude Position: Support Chinese on-screen text as primary requirement (matches existing Yuexia videos)
  - Codex Position: Need clarification on whether Japanese, bilingual overlays, or mixed CJK text must be supported
  - Tradeoff Summary: Chinese-only allows optimized OCR engine selection and preprocessing. Multi-language support requires language detection, multiple OCR models, and more complex text normalization. The draft mentions "中文、日文、英数混排" as a challenge but doesn't specify whether this is required or just acknowledged as a difficulty.
  - Decision Status: **Chinese only** - Focus on Simplified/Traditional Chinese text. OCR engine selection and preprocessing can be optimized for Chinese. English/alphanumeric mixed with Chinese is acceptable as secondary support.

- DEC-4: Manual review budget
  - Claude Position: Build review UI early (Milestone 3) and assume some manual review is acceptable
  - Codex Position: Need explicit budget: what percentage of events can require manual review per hour of video?
  - Tradeoff Summary: Higher review tolerance allows simpler pipeline with more flagged events. Lower review tolerance requires more sophisticated OCR, preprocessing, and fallback mechanisms. This affects the confidence threshold for review_required flag and whether VLM/API fallback is worth implementing.
  - Decision Status: **Less than 5% (minimal review)** - Need highly automated pipeline. This requires sophisticated OCR, preprocessing profiles, multi-engine fusion, and VLM/API fallback for difficult cases. Confidence thresholds must be tuned to minimize false positives while maintaining quality.

- DEC-5: External API usage policy
  - Claude Position: VLM/API fallback is optional and requires explicit user approval
  - Codex Position: Need clear policy on whether copyrighted game frames can be sent to external APIs
  - Tradeoff Summary: Local-only processing is safer for copyright/privacy but limits fallback options to local VLMs (higher GPU requirements) or human review. External API access enables better fallback quality but has legal, privacy, and cost implications. The draft mentions "闭源API调用作为兜底,初次使用需要配置API url和key，如果不配说明放弃兜底" which suggests optional external API is acceptable, but needs explicit confirmation.
  - Decision Status: **Yes, external APIs allowed** - Can send frames to external multimodal APIs (GPT-4V, Claude Vision, etc.) for difficult cases. Must implement cost monitoring and capping to prevent runaway expenses. API configuration is required before fallback is enabled.

- DEC-6: Output format priority
  - Claude Position: Both plain text (for existing prompts) and structured JSONL (for provenance) are required
  - Codex Position: Need clarification on whether new pipeline should replace training data/transcripts/*.txt or produce parallel artifacts
  - Tradeoff Summary: Replacing existing transcripts means the new pipeline becomes the primary extraction method and must match or exceed current quality. Parallel artifacts allow gradual migration and A/B comparison but create workflow complexity (which transcript to use?). The draft doesn't explicitly address this transition strategy.
  - Decision Status: **Replace existing transcripts** - The new OCR pipeline will become the primary extraction method. Must validate quality matches or exceeds current ASR transcripts before full replacement. Existing ASR transcripts can be kept as backup during transition period.

- DEC-7: Quality vs. recall priority
  - Claude Position: Both are important, but acceptance criteria emphasize recall (90%) and CER below threshold
  - Codex Position: Need explicit priority: is exact dialogue wording more important (for persona style) or high-recall story/event capture (for story completeness)?
  - Tradeoff Summary: Optimizing for exact wording favors conservative OCR with high confidence thresholds and more manual review. Optimizing for recall favors aggressive extraction with lower confidence thresholds and more post-processing cleanup. The existing persona_analyzer.md emphasizes preserving original quotes, suggesting wording accuracy is important.
  - Decision Status: **Both equally important** - Need both accurate quotes (for persona style) and complete coverage (for story completeness). This requires balanced confidence thresholds, multi-engine fusion for quality, and VLM/API fallback for difficult cases to maintain both precision and recall.

- DEC-8: Compute resource constraints
  - Claude Position: Assume GPU is available for OCR (draft mentions "你已有 GPU，适合建设本地流程")
  - Codex Position: Need explicit constraints on GPU memory and local compute capacity
  - Tradeoff Summary: High-end GPU allows heavier OCR models, local VLM fallback, and faster processing. Limited GPU requires lighter models, CPU fallback, or cloud API usage. This affects OCR engine selection (task 3) and whether local VLM is feasible.
  - Decision Status: **RTX 4060ti 16GB (local) + L40/H100 (server)** - Local GPU is sufficient for heavy OCR models and local VLM if needed. Server GPUs (L40/H100) available for batch processing but debugging is more difficult. Primary development and testing should use local GPU. Server GPUs can be used for production batch processing of large video sets.

- DEC-9: Alternative data sources
  - Claude Position: Focus on video-only extraction as specified in draft
  - Codex Position: Are there other data sources available: official scripts, fan transcripts, subtitles, emulator text hooks, wiki dumps?
  - Tradeoff Summary: Additional data sources can provide ground truth for validation, weak supervision for correction, or script matching for quality improvement (as mentioned in draft section 2.11). If available, they should be incorporated into the gold standard or validation workflow. If not available, the pipeline must rely solely on OCR quality.
  - Decision Status: **Video only** - No alternative data sources available. The pipeline must rely entirely on OCR quality. Gold standard must be created through manual labeling of video frames.

- DEC-10: Title variation expectations
  - Claude Position: Start with one work (Yuexia), generalize to multiple works in Milestone 4
  - Codex Position: How much title-to-title variation is expected in the next 6 months? This determines whether multi-work config UX is urgent or premature.
  - Tradeoff Summary: If only 1-2 works are planned, manual ROI configuration per work is acceptable. If 5+ works are planned, investing in better config UX, auto-calibration, or UI template detection becomes worthwhile. This affects Milestone 4 scope and priority.
  - Decision Status: **Just Yuexia (1 work)** - Focus on perfecting extraction for Yuexia videos first. Multi-work support infrastructure should be designed in (proper config abstraction), but extensive multi-work UX and auto-calibration can be deferred to future work.

### Impact of User Decisions on Implementation

Based on the resolved decisions, the following adjustments apply to the plan:

**High Automation Requirement (DEC-4: <5% review):**
- Milestone 3 becomes critical, not optional. Multi-engine OCR fusion (task 13) and VLM/API fallback are required to achieve <5% review rate.
- Confidence threshold tuning must be rigorous to minimize false positives while maintaining quality.
- Preprocessing profiles (task 12) must handle semi-transparent backgrounds, outlines, and other difficult cases effectively.

**Platform Architecture (DEC-1: Reusable platform):**
- Configuration system must be well-abstracted from the start (Milestone 2, task 10).
- Code should avoid Yuexia-specific hardcoding and use configuration-driven approach.
- Milestone 4 (multi-work support) remains in scope but can be validated with just Yuexia initially.

**Resolution Normalization Required (DEC-2: Minor variations):**
- Task 4 (video processing infrastructure) must include letterbox detection and resolution normalization.
- ROI coordinates should be resolution-independent or auto-scaled.

**Chinese-Optimized OCR (DEC-3: Chinese only):**
- Task 3 (OCR evaluation) should prioritize engines with strong Chinese support (PaddleOCR, EasyOCR with Chinese models).
- Preprocessing can be optimized for Chinese text characteristics (character density, stroke patterns).

**External API Fallback Enabled (DEC-5: APIs allowed):**
- Task 13 should include external multimodal API integration (GPT-4V, Claude Vision) as fallback option.
- Must implement cost monitoring, capping, and API configuration management.
- API calls should be batched and cached to minimize costs.

**Quality Replacement Target (DEC-6: Replace transcripts + DEC-7: Both precision and recall):**
- Task 11 (MVP validation) must demonstrate quality meets or exceeds existing ASR transcripts.
- Both recall (≥90%) and precision (low CER) must be validated before considering replacement.
- Transition strategy: keep ASR transcripts as backup until OCR quality is proven on full video set.

**GPU Resource Optimization (DEC-8: RTX 4060ti local + server GPUs):**
- Primary development uses local RTX 4060ti (16GB VRAM sufficient for heavy OCR models).
- Task 3 should test OCR engines on local GPU first.
- Local VLM fallback is feasible if needed (16GB can run smaller VLMs).
- Server GPUs (L40/H100) can be used for production batch processing but not for iterative development.

**Video-Only Pipeline (DEC-9: No alternative sources):**
- Gold standard (task 1) must be created through manual video frame labeling.
- No script matching or weak supervision available - OCR quality is the only lever.
- Validation must rely entirely on manually labeled ground truth.

## Implementation Notes

### Code Style Requirements
- Implementation code and comments must NOT contain plan-specific terminology such as "AC-", "Milestone", "Step", "Phase", or similar workflow markers
- These terms are for plan documentation only, not for the resulting codebase
- Use descriptive, domain-appropriate naming in code instead (e.g., "dialogue_event", "roi_config", "ocr_confidence" rather than "AC-1", "Phase-2", "task5")

### Integration Points

**Existing Infrastructure:**
- `yuexia-skill/tools/video_transcriber.py`: Reference for ffmpeg integration, model loading patterns, batch processing structure, and output directory conventions
- `training data/transcripts/*.txt`: Target output directory and filename convention (video_name.txt)
- `yuexia-skill/prompts/story_analyzer.md` and `persona_analyzer.md`: Consumers of the output, expect timestamped dialogue with speaker attribution and original quotes

**New Components:**
- Tool location: `yuexia-skill/tools/dialogue_extractor.py` (or similar name)
- Configuration directory: `yuexia-skill/configs/` for per-work ROI and preprocessing profiles
- Output directory: `training data/transcripts/` for plain text (compatible with existing workflow)
- Structured output directory: `training data/dialogue_events/` for JSONL with provenance
- Review artifacts directory: `training data/review/` for flagged events with frames/crops

### Technology Stack Recommendations

Based on draft research and existing codebase patterns:

**Video Processing:**
- ffmpeg for audio/video manipulation (already used in video_transcriber.py)
- PyAV or OpenCV for frame extraction and decoding
- Decord as alternative for faster random access if needed

**OCR Engines (evaluate in task 3):**
- PaddleOCR: Strong CJK support, GPU acceleration, good for Chinese/Japanese mixed text
- EasyOCR: Good general-purpose option, supports many languages
- RapidOCR: Lightweight, fast, good for real-time or high-throughput scenarios
- Tesseract: Fallback option, widely supported but may need more preprocessing

**Image Processing:**
- OpenCV: Primary library for preprocessing, ROI extraction, change detection
- PIL/Pillow: Alternative for simpler image operations
- NumPy: Array operations for frame comparison and change detection

**Configuration and Data:**
- YAML or JSON for ROI configuration files (user preference)
- JSONL for structured event output (one event per line, easy to stream and append)
- Plain text for compatibility output (existing transcript format)

### Critical Success Factors

From Codex analysis, these factors are essential for project success:

1. **Build gold standard first**: Without ground truth, there's no way to validate whether the approach works. This is the highest-priority de-risking activity.

2. **Prove OCR viability on real crops before expanding architecture**: Test actual dialogue box crops from target videos with candidate OCR engines before committing to multi-engine, multi-stage, or complex preprocessing strategies.

3. **Define data contract into existing character-building prompts early**: Ensure output format is validated with story_analyzer.md and persona_analyzer.md before building the full pipeline.

4. **Treat reviewability and provenance as core features, not polish**: Low-confidence events need human review with frame/crop access. Build this early to enable debugging and quality validation.

5. **Start simple, enhance iteratively**: Single OCR engine, single preprocessing profile, one target work. Add complexity only after measuring where simple approach fails.

### Risk Mitigation Strategies

**Risk: OCR quality on stylized game text**
- Mitigation: Task 3 evaluates OCR engines on real crops before implementation. Task 12 adds preprocessing profiles for difficult cases. Task 15 provides review UI for manual correction.

**Risk: Event aggregation fragility (typewriter, auto-advance, interruptions)**
- Mitigation: Task 6 implements prefix-growth detection with explicit state machine. Task 11 validates on gold standard to catch edge cases early.

**Risk: Speaker attribution silent failures**
- Mitigation: Task 7 implements explicit handling for empty name boxes, aliases, and special speakers. Task 14 adds normalization dictionary. Task 15 allows manual correction.

**Risk: Integration with existing workflow**
- Mitigation: Task 9 produces output in existing transcript format. Task 17 validates with actual story_analyzer and persona_analyzer prompts.

**Risk: Storage and throughput bottlenecks**
- Mitigation: Task 10 implements checkpointing and resume support. Review artifacts are saved only for flagged events, not all frames.

**Risk: Building wrong thing without validation**
- Mitigation: Milestone 1 creates gold standard and validates OCR before any pipeline implementation. Task 11 measures against acceptance criteria before adding enhancements.

## Output File Convention

This template is used to produce the main output file (e.g., `plan.md`).

### Translated Language Variant

When `alternative_plan_language` resolves to a supported language name through merged config loading, a translated variant of the output file is also written after the main file. Humanize loads config from merged layers in this order: default config, optional user config, then optional project config; `alternative_plan_language` may be set at any of those layers. The variant filename is constructed by inserting `_<code>` (the ISO 639-1 code from the built-in mapping table) immediately before the file extension:

- `plan.md` becomes `plan_<code>.md` (e.g. `plan_zh.md` for Chinese, `plan_ko.md` for Korean)
- `docs/my-plan.md` becomes `docs/my-plan_<code>.md`
- `output` (no extension) becomes `output_<code>`

The translated variant file contains a full translation of the main plan file's current content in the configured language. All identifiers (`AC-*`, task IDs, file paths, API names, command flags) remain unchanged, as they are language-neutral.

When `alternative_plan_language` is empty, absent, set to `"English"`, or set to an unsupported language, no translated variant is written. Humanize does not auto-create `.humanize/config.json` when no project config file is present.

--- Original Design Draft Start ---

# ACGN 长视频对话抽取技术方案报告（Markdown 版）

- 文档版本：v1.0
- 调研日期：2026-04-08
- 面向场景：ACGN / Galgame / 视觉小说风格长视频，对话框+人名框为主，夹杂战斗演出、CG、实机演示、旁白/系统字样，部分有配音、部分无配音。
- 输出目标：高质量导出为**纯文本对话台本**，尽量保持 `说话人：台词` 结构，并支持后续人工复核与迭代优化。

---

## 重要！！！

前情提要：当前项目为借鉴同事.skill所做的ACGN-character.skill，虽然当前的命名为yuexia.skill，之前根据training data里面的视频数据，通过ASR提取其中的对话，参考同事.skill的蒸馏数据的方法，得到的月下的人格与故事设定，然而ASR的方案对于人物数据来说并不高质量，部分视频没有配音导致无法提取到语音，此外各种杂音和BGM导致提取质量很差。因此我们需要有一个工具来将视频中的对话文本给高质量的提取出来作为纯文本内容。为了解决这个需求，才有了下面的这个方案报告。从视频到对话台本，这是当前ACGN-character.skill的其中一个工具，是封装在这个skill里面的，是作为其中的一个功能来使用的。之后对于ACGN中的Anime， Comic， Game， Novel都需要进行一定程度的支持，目前就只做这种视频处理即可。本方案中存在大量的参考项目链接，对于github上的开源项目，需要查看的，通过git clone到本地，然后再本地查看，不要通过web fetch，git clone之后，记得把其中的.git文件夹删掉，不然会导致git in git问题。如果github访问不到，使用代理，本机的10808端口。

## 目录

1. [执行摘要](#执行摘要)
2. [需求分析](#需求分析)
3. [问题拆解与关键难点](#问题拆解与关键难点)
4. [社区调研结果](#社区调研结果)
5. [应参考的开源项目与借鉴点](#应参考的开源项目与借鉴点)
6. [总体技术路线](#总体技术路线)
7. [可执行的项目总纲](#可执行的项目总纲)
8. [详细技术方案](#详细技术方案)
9. [数据结构与输出格式](#数据结构与输出格式)
10. [质量评估与验收标准](#质量评估与验收标准)
11. [成本、性能与部署策略](#成本性能与部署策略)
12. [风险清单与应对措施](#风险清单与应对措施)
13. [实施路线图](#实施路线图)
14. [推荐结论](#推荐结论)
15. [参考资料](#参考资料)

---

## 执行摘要

### 一句话结论

对你的场景，**最佳路线不是 ASR-first，而是 OCR-first**：  
以**视频画面中的对话框文字**为主信息源，围绕“**对话事件**”而不是“单帧文本”做处理；再以 **ASR** 作为配音句的辅助校对，以 **VLM / 闭源多模态 API** 作为**低置信度难例兜底**，最终输出结构化台本。

### 为什么不是直接用 Whisper
因为你的目标不是“听清说了什么”，而是“拿到视频里展示出来的正式文本”：

1. 有些句子没有配音，ASR 天然拿不到。
2. BGM、音效、战斗演出会显著污染语音识别。
3. 视觉小说式内容的真正“权威文本”通常已经出现在对话框中。
4. 你要的是**小说台本结构**，而不是一段时间轴转写。

### 方案核心
本报告建议构建一条四层流水线：

1. **对话段识别层**：从长视频里找出“处于对话界面”的片段。
2. **视觉抽取层**：只对名字框 / 对话框 ROI 做 OCR，并做多帧融合。
3. **结构化整理层**：将 OCR 结果整理为“说话人 + 台词 + 事件类型”。
4. **增强兜底层**：ASR 校对有声句；VLM/API 只处理低置信度难例；必要时引入脚本匹配。

最终形成一条“**本地为主、API 为辅、可批处理、可人工复核**”的高质量生产线。

---

## 需求分析

## 1. 业务目标

需要把数小时级别的 ACGN 视频中的对话内容提取为纯文本台本，要求：

- 尽可能完整地提取视频中出现的对白内容；
- 保持视觉小说的对话格式，而不是纯散文或字幕流；
- 对于显示了名字的人物，输出其角色名；
- 对于旁白、系统提示、选项、演出字幕等，保留相应标签；
- 最终适合作为小说脚本、文本存档、后续翻译或检索的基础数据。

## 2. 输入特征

输入视频具有以下特征：

- 以视觉小说式站桩对话为主；
- 存在固定或半固定的**人名框 + 对话框**布局；
- 有些视频会夹杂战斗、CG 演出、实机操作或 UI 过场；
- 有些句子有配音，有些没有；
- 对话框可能存在：
  - 半透明背景；
  - 描边字 / 阴影字；
  - 打字机逐字展开；
  - 长句跨多帧停留；
  - 不同 UI 主题、不同分辨率；
  - 名字框有时为空，或者使用“旁白 / 系统 / ???”。

## 3. 输出要求

最终输出不应只是 OCR 原文堆叠，而应尽可能接近：

```text
莉莉娅：梦莎莉娅梦莎莉娅，是的没错，他醒了。
梦莎莉娅：……
【旁白】夜色渐深，风声渐起。
【系统】获得道具：XXXX
【选项】1. 留下  2. 离开
```

并建议同时保留一份中间结构化数据，用于复核：

```json
{
  "event_id": "ep01_000123",
  "source_file": "path/to/source_file",
  "start_ms": 123456,
  "end_ms": 126980,
  "scene_type": "dialogue",
  "speaker": "莉莉娅",
  "text": "梦莎莉娅梦莎莉娅，是的没错，他醒了。",
  "ocr_confidence": 0.93,
  "asr_text": null,
  "review_required": false
}
```

## 4. 非功能性要求

- **高质量优先**：宁可慢一些，也要保证台本可用性。
- **批处理能力**：能处理数小时视频，不要求实时。
- **成本可控**：闭源 API 不能作为全量主链路。
- **可扩展**：后续应能适配不同作品、不同 UI。
- **可复核**：低置信度结果必须能回看和人工修正。
- **本地部署优先**：你已有 GPU，适合建设本地流程。

---

## 问题拆解与关键难点

## 1. 不能把“每帧 OCR”当成最终方案

视觉小说对话的最常见陷阱是：

- 同一句话会停留几十到几百帧；
- 前若干帧只是打字机残句；
- 转场帧会出现运动模糊；
- 对话框不变但角色表情变了；
- 一句话可能跨 1–3 秒稳定显示。

因此系统必须围绕“**对话事件**”而不是“每帧识别”来工作。

## 2. OCR 难点比 ASR 更复杂，但更接近真实文本

OCR 面临的主要问题：

- 半透明底板；
- 动画模糊；
- 字体描边 / 发光 / 阴影；
- 小字、多行、挤压换行；
- 立绘、特效、背景图干扰；
- 中文、日文、英数混排；
- 名字框与正文区样式不同。

但即便如此，对你的任务，OCR 仍然是主线，因为它能覆盖**无配音句**和**正式显示文本**。

## 3. 说话人识别并不等于单纯读名字框

说话人识别会遇到：

- 名字框缺失；
- 同一角色多种写法；
- `？？？`、`旁白`、`系统` 等特殊说话人；
- 战斗场景中台词直接浮在 HUD 或演出字幕里；
- UI 切换后名字框位置变化。

因此需要一套**说话人判定优先级策略**，而不是单次 OCR 读个名字就结束。

## 4. 社区已有工具多为“单点强项”，不是完整生产线

这一点很关键。社区里已经有：

- 擅长 Hook 的；
- 擅长实时 OCR 的；
- 擅长视频硬字幕抽取的；
- 擅长 ROI 批量图像 OCR 的；
- 擅长脚本匹配的；

但没有一个成熟开源项目能直接满足“**长视频 -> 高质量 VN 对话台本**”这一端到端需求。因此本方案的价值，不在于再造轮子，而在于**组合这些成熟思路**。

---

## 社区调研结果

## 1. 调研结论概览

对本需求最相关的社区资产大致分为四类：

### A. Hook / 文本抓取类
- **Textractor**：典型 VN 文本 hook 工具，可直接从游戏进程提取文本。[Textractor GitHub](https://github.com/Artikash/Textractor)
- **LunaTranslator**：将 HOOK、OCR、模拟器支持、翻译、TTS 等集成到同一工具链中，强调 HOOK 仍是主要提取方式。[LunaTranslator 官网](https://docs.lunatranslator.org/en/) / [GitHub](https://github.com/HIllya51/LunaTranslator)

### B. 实时 OCR / 读屏类
- **GameSentenceMiner (GSM)**：最值得借鉴的是其两阶段 OCR 设计，先快扫检测变化，再精扫定稿。[GSM OCR 文档](https://docs.gamesentenceminer.com/docs/features/ocr/)
- **OwOCR**：多输入、多输出，支持 screen capture / OBS / websocket，且内建“两阶段”实用思路。[OwOCR GitHub](https://github.com/AuroraWright/owocr)
- **Visual Novel OCR**：重点解决视觉小说常见的半透明背景问题，依赖 mirror capture + color contrast threshold 思路。[Visual Novel OCR Guide](https://visual-novel-ocr.sourceforge.io/)
- **RSTGameTranslation**：更贴近你的目标，支持多 OCR 引擎、上下文感知、角色/地点名识别、chat window 场景优化。[RSTGameTranslation 官网](https://thanhkeke97.github.io/RSTGameTranslation/) / [GitHub](https://github.com/thanhkeke97/RSTGameTranslation)

### C. 视频 OCR / 硬字幕批处理类
- **VideOCR**：典型的硬字幕视频 OCR 骨架，适合参考其长视频批处理方式。[VideOCR GitHub](https://github.com/timminator/VideOCR)
- **VideoSubFinder**：擅长先从视频中找出带字帧，再生成清背景文字图供 OCR 使用。[VideoSubFinder SourceForge](https://sourceforge.net/projects/videosubfinder/)
- **visual-novel-game-ocr**：思路来自 videocr，强调关键帧筛选、RapidOCR、输出 txt/SRT/docx，非常适合参考其“只处理变化帧”的理念。[visual-novel-game-ocr GitHub](https://github.com/wanghaisheng/visual-novel-game-ocr)
- **video-text-extraction**：明确面向 visual novel gameplay video，强调去重和固定 ROI。[video-text-extraction GitHub](https://github.com/girubato/video-text-extraction)

### D. 结构化 / 脚本匹配 / ROI 工具类
- **GameDialogueOCR**：适合借鉴其“自定义 ROI + 批量 OCR”的交互方式。[GameDialogueOCR GitHub](https://github.com/purpyviolet/GameDialogueOCR)
- **Game2Text**：值得借鉴的是 OCR-assisted game script matching；如果能找到现成脚本，可显著提纯结果。[Game2Text GitHub](https://github.com/mathewthe2/Game2Text)

## 2. 调研结论的核心判断

从上述项目的官方说明可以看出：

- Hook 工具适合“有原游戏进程”的场景，不适合“只有录制视频”的场景；
- 实时 OCR 工具最贴近“VN 画面读字”的核心问题；
- 视频 OCR 工具最贴近“长视频离线批处理”的工程模式；
- 结构化工具和脚本匹配适合作为质量增强模块。

因此，**社区现状并不是缺“可借鉴的方案”，而是缺“能直接产出台本的一体化工程整合”**。  
这意味着最合理的选择是：**参考成熟项目的核心机制，自建一条围绕 VN 对话事件的批处理流水线**。

---

## 应参考的开源项目与借鉴点

下表给出“建议参考，不建议直接照搬”的清单。

| 项目                    | 重点借鉴内容                            | 不建议直接作为最终产品的原因          |
| --------------------- | --------------------------------- | ----------------------- |
| Textractor            | Hook 文本抓取思路；有原游戏时可作为旁路真值来源        | 你的主场景是视频，不是原游戏进程        |
| LunaTranslator        | HOOK + OCR + 翻译聚合框架，说明多来源融合是成熟路线  | 更偏实时阅读/翻译，不是长视频批量出剧本    |
| GameSentenceMiner     | **两阶段 OCR**：OCR1 快扫变化，OCR2 稳定后精扫  | 当前目标是离线批处理，不是实时学习工具     |
| OwOCR                 | 多输入源、多输出、两阶段优化、重复过滤               | 更偏实时通用 OCR，不含 VN 台本级后处理 |
| Visual Novel OCR      | **半透明对话框处理**、镜像截取、色彩阈值            | 依赖手动设置，自动化批处理能力不足       |
| RSTGameTranslation    | **上下文感知、角色名识别、VN chat window 优化** | 主要目标是翻译显示，不是脚本导出系统      |
| VideOCR               | 长视频硬字幕抽取骨架、CLI/GUI 批处理思路          | 不理解人物名框和 VN 结构          |
| VideoSubFinder        | **先找有字帧，再做 OCR** 的思想非常重要          | 面向通用 hardsub，不理解对话格式    |
| visual-novel-game-ocr | 变化帧筛选、RapidOCR、离线输出 txt/SRT/docx  | 更像轻量实验/工具，不是完整生产线       |
| video-text-extraction | 固定 ROI + 去重逻辑，很贴近 VN 视频           | 结构简单，OCR 与后处理能力有限       |
| GameDialogueOCR       | 自定义 ROI 标注与批处理交互                  | 主要是图像级工具，不是长视频系统        |
| Game2Text             | **OCR 辅助脚本匹配**                    | 依赖脚本库，不适合无脚本的通用主链路      |

---

## 总体技术路线

## 1. 路线选择

### 结论
采用：

> **OCR-first + 事件聚合 + 结构化后处理 + ASR/VLM 兜底**

而不是：

- 纯 ASR；
- 纯单帧 OCR；
- 纯闭源多模态 API；
- 纯规则脚本；
- 纯 Hook。

## 2. 设计原则

### 原则一：围绕“对话事件”建模
一个事件是“一条完整台词在屏幕上从出现到消失的过程”，而不是“一帧图像”。

### 原则二：先找“对话段”，再识别“对话框”
整个视频不必逐帧高精 OCR，先分出有效时段，再精处理。

### 原则三：固定 UI 的作品，必须允许每作单独标定 ROI
这会极大提升质量，远胜于盲跑通用 OCR。

### 原则四：低成本模块处理大多数样本，高成本模块只处理难例
大部分样本走本地 OCR；只有低置信度片段才调 VLM 或闭源 API。

### 原则五：保留中间结果，支持复核
任何高质量转录系统都应该保留：
- 帧图；
- ROI 图；
- OCR 候选；
- 置信度；
- ASR/VLM 候选；
- 最终定稿。

---

## 可执行的项目总纲

## 1. 目标成果物

本项目最终将工具封装在skill中，因此对用户来说对外成果物只是一个skill。而对于skill来说，最终至少产出以下成果：

1. **批处理命令行工具**
   - 输入：视频文件、作品配置
   - 输出：TXT / Markdown / JSONL / SRT（可选）

2. **作品配置系统**
   - 每部作品维护一份 UI 配置
   - 包括名字框 ROI、对话框 ROI、文本颜色策略、特殊场景策略

3. **低置信度复核工具**
   - 能看原帧、OCR 候选、最终文本
   - 支持手工修正

4. **评测与回归集**
   - 选取若干视频片段做人标金标
   - 用于评估完整率、错字率、重复率、说话人准确率

## 2. 交付阶段

### P0：PoC 验证版
目标：验证核心假设成立。

交付：
- 1 部作品，10–20 分钟样本；
- 能从标准对话 UI 中抽出台词；
- 能处理打字机展开；
- 能输出基本台本。

### P1：MVP 批处理版
目标：形成可跑完整视频的基本系统。

交付：
- 对话段检测；
- ROI OCR；
- 多帧融合；
- 去重；
- 说话人提取；
- TXT/Markdown 导出；
- 低置信度标记。

### P2：高质量增强版
目标：大幅提高复杂场景可用性。

交付：
- 半透明背景增强；
- 多 OCR 引擎策略；
- ASR 辅助；
- 本地/闭源 VLM 兜底；
- 战斗/CG/系统文本分类。

### P3：生产化版
目标：支持多作品、多视频、低人工成本。

交付：
- 作品配置管理；
- 批量队列；
- 复核 UI；
- 质量报表；
- 版本化输出与回归测试。

---

## 详细技术方案

## 1. 总体架构

```text
视频输入
  -> 视频解码与抽样
  -> 场景分类 / 对话段检测
  -> ROI 裁切（名字框、对话框）
  -> 文本变化检测
  -> OCR1 快扫
  -> 稳定判定
  -> OCR2 精扫
  -> 事件聚合（去重、残句合并、定稿）
  -> 说话人识别 / 特殊角色识别
  -> ASR 辅助（可选）
  -> VLM/API 低置信度兜底（可选）
  -> 文本清洗 / 结构化导出
  -> 人工复核
  -> 最终台本
```

---

## 2. 模块设计

## 2.1 视频解码与抽样模块

### 目标
高效读取长视频，并为后续分析提供稳定帧序列。

### 建议
- 使用 `ffmpeg` 做基础预处理；
- 使用 `PyAV` 或 `Decord` 做高效视频解码；
- 先按较低频率做粗扫描，例如 2–6 fps；
- 在检测到对话段后，再切换到较高频率分析，例如 8–15 fps。

### 原因
并不是所有帧都值得高成本处理。`visual-novel-game-ocr` 明确强调**不需要对整段视频每帧都做 OCR，而应先筛出变化关键帧**，这一点非常适合你的场景。[visual-novel-game-ocr GitHub](https://github.com/wanghaisheng/visual-novel-game-ocr)

---

## 2.2 对话段检测模块

### 目标
从整段视频中识别出“标准对话 UI 正在显示”的时段。

### 建议的实现层次

#### 第一层：轻规则
- 检测底部大矩形对话框区域的存在；
- 检测名字框位置的样式特征；
- 统计字幕框区域边缘 / 半透明层 / 固定色块特征；
- 用 OCR 探测框中是否持续有文本。

#### 第二层：轻量分类器
训练一个二分类模型：
- `dialogue_ui`
- `non_dialogue_ui`

输入可为整帧缩略图或底部区域图。

#### 第三层：多类场景分类器（进阶）
分类：
- 标准对话
- CG 演出字幕
- 战斗 HUD 对话
- 系统提示
- 选项界面
- 非文本场景

### 推荐策略
PoC 阶段用规则；P2 阶段再加分类器。

---

## 2.3 ROI 管理模块

### 目标
只对真正有意义的区域做 OCR。

### 为什么很重要
相比整帧 OCR，固定 ROI 的稳定性会高很多；而且人名框和正文区最好分开处理，因为字体、尺寸、色彩、文本长度都不同。

### 设计建议
每部作品配置一份 YAML / JSON：

```yaml
game_id: sample_vn
resolution_profile:
  base_width: 1920
  base_height: 1080
name_box:
  x: 95
  y: 708
  w: 210
  h: 78
dialog_box:
  x: 90
  y: 785
  w: 1540
  h: 170
special_modes:
  battle_caption:
    enabled: true
    roi: [120, 820, 1480, 120]
preprocess_profile:
  mode: semi_transparent_hsv
```

### 参考来源
- `GameDialogueOCR` 证明了**自定义 ROI**对文字游戏和 VN 转录很有价值。[GameDialogueOCR GitHub](https://github.com/purpyviolet/GameDialogueOCR)
- `video-text-extraction` 也明确依赖“text boxes are always in the same positions”这一假设。[video-text-extraction GitHub](https://github.com/girubato/video-text-extraction)

---

## 2.4 文本变化检测模块

### 目标
识别“这是不是一条新对白”。

### 关键思路
参考 `GameSentenceMiner` 的两阶段设计：  
先用快引擎持续扫描文本变化，稳定后再精识别。[GSM OCR 文档](https://docs.gamesentenceminer.com/docs/features/ocr/)

### 实现建议

在 ROI 上维护时间序列：

- 图像差分：比较当前对话框 ROI 与上一时刻是否变化；
- OCR 文本差分：比较候选文本是否增长/变化；
- 稳定判定：若连续 N 帧差异低于阈值，则视为“文本稳定”。

### 打字机判定
若文本呈现“前缀扩展”模式，例如：

- `梦莎`
- `梦莎莉娅`
- `梦莎莉娅梦莎`
- `梦莎莉娅梦莎莉娅，是的没错，他醒了。`

则这些帧应归入同一个事件，而不是多条句子。

### 推荐规则
- 文字长度单调增加；
- 新文本以前一帧文本为前缀；
- 字框变化小；
- 时间间隔短。

满足上述条件时，执行“事件内合并”。

---

## 2.5 图像预处理模块

### 目标
把复杂的对话框图像转成 OCR 更容易识别的图。

### 必做预处理
- 放大（1.5x–3x）
- 锐化
- 去噪
- 自适应二值化
- 颜色通道分离
- 描边/阴影减弱

### 半透明背景处理
这里应重点借鉴 `Visual Novel OCR` 的思路：  
其核心不是简单截图，而是通过**镜像截取**与**色彩对比阈值**，把半透明背景上的文字转成更清晰的“深色字 / 浅底”图像。[Visual Novel OCR Guide](https://visual-novel-ocr.sourceforge.io/)

### 建议的预处理配置模式
为不同作品预设多种 profile：

- `plain_light_bg`
- `plain_dark_bg`
- `semi_transparent_hsv`
- `outline_heavy`
- `battle_caption`
- `small_font_dense`

每个 profile 对应不同的阈值和形态学处理参数。

---

## 2.6 OCR 模块

### 总体建议
不要只用一个 OCR 引擎，也不要让所有帧都走最重模型。  
推荐采用：

- **OCR1（快）**：用于变化检测、粗识别、低成本扫描
- **OCR2（准）**：用于稳定后定稿

这与 `GameSentenceMiner` 以及 `OwOCR` 的“两阶段”思路一致。[GSM OCR 文档](https://docs.gamesentenceminer.com/docs/features/ocr/) [OwOCR GitHub](https://github.com/AuroraWright/owocr)

### 推荐实现
- OCR1：本地轻量 OCR（适合高频调用）
- OCR2：更高精度 OCR（可用 GPU）
- 对名字框和正文框使用不同参数，必要时甚至使用不同 OCR 引擎

### 候选融合
对每个事件保留多个 OCR 候选：
- 原始候选；
- 预处理版本候选；
- 多引擎候选；
- 多帧候选。

最终使用投票或规则融合定稿。

---

## 2.7 事件聚合与定稿模块

这是整个系统里**最重要**的模块之一。

### 输入
若干帧的名字框 OCR、正文 OCR、图像变化信息、时间戳。

### 输出
一条稳定的结构化对话事件。

### 核心职责

#### 1）去重复
同一句话停留很多帧时，只保留一次。

#### 2）残句合并
打字机效果的中间残句不输出，只保留最终完整句。

#### 3）候选排序
优先级建议：

1. 长度更完整；
2. 与前缀增长链一致；
3. OCR 置信度更高；
4. 跨帧一致性更强；
5. 与角色/上下文更合理。

#### 4）换行与标点修复
常见修复包括：
- 多余空格；
- OCR 把中文标点识别为英标；
- 行末错误断句；
- 重复字；
- 省略号形态统一。

### 建议的事件状态机

```text
Idle
  -> Detecting (发现文本)
  -> Growing (文本在增加，疑似打字机)
  -> Stable (文本稳定，等待定稿)
  -> Finalized (输出事件)
  -> Idle
```

---

## 2.8 说话人识别模块

### 优先级策略

#### 优先级 1：明确名字框
若当前事件名字框识别到稳定角色名，则直接采用。

#### 优先级 2：特殊标签
若正文区域显示：
- `旁白`
- `系统`
- `？？？`
- `选择`
  则按规则赋予标签。

#### 优先级 3：连续继承
若名字框缺失，但当前事件与上一事件属于同一对话界面、同一文本风格，且中间没有明显角色切换，则可继承上一说话人。

#### 优先级 4：未知保留
若仍不确定，则输出：
- `【未知】`
- 或保留空 speaker 字段，进入复核队列。

### 角色名词典
建议建立每部作品的角色名 alias 表：

```yaml
speaker_alias:
  梦莎莉娅: [梦莎莉娅, 梦莎, 莎莉娅]
  莉莉娅: [莉莉娅, 莉莉亚]
  系统: [系统, SYS, System]
```

这会显著提升说话人归一化能力。

### 借鉴来源
`RSTGameTranslation` 明确强调其有“game context awareness and character name detection”，这说明上下文和名称识别在游戏 OCR 中是很有价值的方向。[RSTGameTranslation GitHub](https://github.com/thanhkeke97/RSTGameTranslation)  
其官网也写明支持 visual novel chat window，并通过上下文信息提升识别/翻译准确性。[RSTGameTranslation 官网](https://thanhkeke97.github.io/RSTGameTranslation/)

---

## 2.9 ASR 辅助模块（不选，放弃这条路）

### 定位
ASR 只做三件事：

1. 给有配音的事件提供校对候选；
2. 在 OCR 置信度很低时提供补全文本；
3. 帮助判断台词切换边界。

### 不应该做的事
- 不要直接把整段音频转成最终台本；
- 不要把无配音台词交给 ASR；
- 不要在 BGM 很重的战斗场景里盲目信任 ASR。

### 建议流程
- 先做人声分离 / 降噪；
- 只在“对话事件时段”内调用 ASR；
- 将 ASR 结果与 OCR 结果对齐；
- 仅在 OCR 不稳时才参考 ASR。

---

## 2.10 VLM / 闭源多模态 API 兜底模块（闭源API调用作为兜底,初次使用需要配置API url和key，如果不配说明放弃兜底）

### 目标
解决以下难例：

- OCR 两个引擎都不稳定；
- 半透明或演出特效导致字符缺损；
- 名字框过小或颜色复杂；
- 系统字、战斗 HUD 字、CG 演出文字难以用普通 OCR 捕捉。

### 成本控制原则
只在以下条件满足时才触发：

- OCR 置信度低于阈值；
- 多帧候选相互冲突；
- 名字框缺失但判断有说话人；
- 事件被标为 `review_required`。

### 输入方式
不要把整段长视频交给 API。  
只发送与单条事件相关的 2–5 张关键帧，以及结构化提示词，例如：

```text
请只转写该游戏画面中对话框的最终完整文本，并分别输出：
speaker:
text:
不要翻译，不要解释，不要补写未显示内容。
```

### 推荐定位
- 本地 VLM：中等成本、适合大量难例
- 闭源 API：高成本，只处理最终顽固难例

---

## 2.11 脚本匹配模块（不用看这个，这个不需要，没有脚本）

如果对应作品能找到现成剧本、攻略文本或玩家整理文本，那么可引入“脚本匹配”层：

1. 用 OCR 得到近似句；
2. 在脚本库中做模糊匹配；
3. 匹配到高相似度句后，以脚本句替换 OCR 结果；
4. 说话人也可由脚本侧补全。

这条路线直接借鉴 `Game2Text` 的 “OCR-assisted game script matching”。[Game2Text GitHub](https://github.com/mathewthe2/Game2Text)

注意：这是**增强层**，不是主链路；因为很多视频未必有现成脚本。

---

## 2.12 人工复核模块

### 必要性
只要目标是“高质量台本”，就必须给低置信度结果留人工通道。

### 建议复核界面展示
- 事件编号；
- 起止时间；
- 原始关键帧；
- 名字框 ROI 图；
- 正文框 ROI 图；
- OCR 候选 1 / 2 / 3；
- ASR 候选；
- VLM 候选；
- 当前定稿；
- 标记理由（如“名字不稳”“疑似残句”“疑似系统字”）。

### 复核优先队列
优先人工处理：
1. 低置信度；
2. 名字缺失；
3. 多候选冲突；
4. 高价值剧情段；
5. 选项 / 分支文本。

---

## 数据结构与输出格式

## 1. 中间结构：JSONL

建议所有事件都先写入 JSONL，以支持追踪和复跑。

```json
{
  "video_id": "sample_ep01",
  "event_id": "sample_ep01_000245",
  "scene_type": "dialogue",
  "start_ms": 183240,
  "end_ms": 186960,
  "speaker_raw": "莉莉娅",
  "speaker_norm": "莉莉娅",
  "text_raw": "梦莎莉娅梦莎莉娅，是的没错，他醒了。",
  "text_norm": "梦莎莉娅梦莎莉娅，是的没错，他醒了。",
  "ocr_candidates": [
    {"engine": "ocr1", "text": "梦莎莉娅梦莎莉娅，是的没错，他醒了。", "conf": 0.88},
    {"engine": "ocr2", "text": "梦莎莉娅梦莎莉娅，是的没错，他醒了。", "conf": 0.95}
  ],
  "asr_text": null,
  "vlm_text": null,
  "final_confidence": 0.95,
  "review_required": false
}
```

## 2. 最终输出：Markdown / TXT

### Markdown
```markdown
# sample_ep01

莉莉娅：梦莎莉娅梦莎莉娅，是的没错，他醒了。  
梦莎莉娅：……  
【旁白】夜色渐深，风声渐起。  
【系统】获得道具：XXXX  
```

### TXT
```text
莉莉娅：梦莎莉娅梦莎莉娅，是的没错，他醒了。
梦莎莉娅：……
【旁白】夜色渐深，风声渐起。
【系统】获得道具：XXXX
```

### 可选：SRT
用于回看时间轴，不作为主交付。

---

## 质量评估与验收标准

**以下质量评估以及验收标准仅供参考，构造标准集和检查各种指标需要大量的人工，因此实际并不现实。真正现实的人工验收应该是读取处理后的结构化的对话文本，通过html页面可视化的方式供用户审阅，可以给每条记录进行打分与评价，选择好还是不好。**

## 1. 建议建立金标集

从每部作品抽取 10–30 分钟，人工标注：
- 对话起止时间；
- 说话人；
- 文本；
- 特殊事件类型（旁白/系统/选项/战斗字幕）。

## 2. 建议指标

### 文本层
- **字符错误率（CER）**
- **句子完整率**：是否抽到了完整句，而不是残句
- **重复率**：同一句多次输出的比例

### 结构层
- **说话人准确率**
- **事件切分准确率**
- **特殊标签准确率**（旁白/系统/选项）

### 系统层
- **每小时视频处理时长**
- **每小时视频 API 成本**
- **需人工复核比例**

## 3. 验收建议

### MVP 验收
- 标准对话 UI 场景下，文本完整率达到可读水平；
- 打字机重复显著减少；
- 能导出成基础台本。

### 高质量验收
- 核心剧情段文本可直接阅读；
- 大多数说话人可正确识别；
- API 成本可控；
- 人工只需处理少量难例。

---

## 成本性能与部署策略

## 1. 成本控制原则

### 本地优先
- 视频解码、本地 OCR、规则后处理全部本地执行；
- 本地 GPU 优先给高精度 OCR 或本地 VLM 使用。

### API 只用于难例
闭源多模态 API 的使用门槛建议设置为：
- 最终置信度 < 阈值；
- 事件被标记为重要剧情；
- 或人工复核成本高于 API 成本。

## 2. 性能优化原则

- 先粗扫、后精扫；
- 先场景筛选、后 OCR；
- 只处理 ROI，不处理整帧；
- 只在变化帧和稳定阶段做精识别；
- 对同一作品缓存配置与词典；
- 支持断点续跑。

## 3. 推荐部署形态

### 单机本地部署
适合当前阶段：
- 一台带 GPU 的 Windows / Linux 工作站；
- 本地存视频、本地跑 OCR、本地出 JSONL/TXT；
- 可选连接云端 API。

### 后续可扩展
如果后面视频量很大，可以升级为：
- 解码与场景切分：CPU 队列
- OCR/VLM：GPU 队列
- 复核界面：本地 Web 前端

---

## 风险清单与应对措施

| 风险             | 表现         | 应对                             |
| -------------- | ---------- | ------------------------------ |
| 打字机残句过多        | 台本出现大量半句   | 事件状态机 + 前缀增长合并                 |
| 半透明背景识别差       | 漏字、错字      | 借鉴 Visual Novel OCR 的阈值/颜色增强思路 |
| 战斗/CG 场景误检     | 非对白文本混入主台本 | 场景分类 + 特殊标签输出                  |
| 名字框不稳定         | 说话人错乱      | 角色词典 + 上下文继承 + 低置信度复核          |
| OCR 对某作品字体不适配  | 错字率高       | 每作 profile、双引擎、多预处理策略          |
| 闭源 API 成本失控    | 预算不可控      | 只对低置信度事件调用                     |
| 无码脚本匹配不可用      | 无法进一步提纯    | 将 script matching 作为增强项而非依赖项   |
| 长视频重复处理成本高     | 处理慢        | 变化帧筛选、断点续跑、缓存                  |
| 直接复用开源代码产生许可问题 | 商业或分发风险    | 先借鉴设计，直接集成代码前逐仓核查许可协议          |

---

## 实施路线图

## 第一阶段：2 周内完成 PoC

### 目标
验证：
- 对话段识别可行；
- ROI OCR 可行；
- 打字机合并可行；
- 基础台本输出可行。

### 工作项
1. 选 1 部作品样本视频（10–20 分钟）
2. 人工标定 ROI
3. 实现：
   - 视频解码
   - 对话段检测（规则版）
   - OCR1/OCR2
   - 事件聚合
   - TXT/Markdown 导出
4. 产出误差报告

## 第二阶段：3–5 周完成 MVP

### 目标
跑完整数小时视频，人工修一点就能用。

### 工作项
- 增加作品配置系统
- 增加角色词典
- 增加特殊事件标签
- 增加复核输出
- 支持断点续跑
- 输出 JSONL + Markdown

## 第三阶段：5–8 周完成高质量增强

### 工作项
- 半透明背景增强 profile
- 多 OCR 引擎融合
- ASR 辅助
- 本地 VLM / API 兜底
- 战斗/CG/系统场景分类
- 评测集与回归测试

## 第四阶段：后续迭代

### 工作项
- 多作品配置沉淀
- 脚本匹配增强
- 可视化复核前端
- 质量报表
- 批量任务管理

---

## 推荐结论

## 1. 最推荐的工程路线

### 主链路
**视频场景筛选 -> 固定 ROI OCR -> 文本变化检测 -> 多帧融合 -> 说话人结构化 -> 导出台本**

### 兜底链路
**低置信度事件 -> 闭源多模态 API**

---

## 2. 最值得直接借鉴的开源项目

### 必借鉴
1. **GameSentenceMiner**
   - 借“两阶段 OCR”与“文本稳定后再定稿”的思路。  
   - 这是解决打字机问题最关键的参考之一。  
     参考：[GSM OCR 文档](https://docs.gamesentenceminer.com/docs/features/ocr/)

2. **Visual Novel OCR**
   - 借“半透明背景处理”和“色彩对比阈值”思路。  
   - 这是提高对话框 OCR 质量的关键参考之一。  
     参考：[Visual Novel OCR Guide](https://visual-novel-ocr.sourceforge.io/)

3. **RSTGameTranslation**
   - 借“上下文感知、角色名识别、VN chat window 优化”的思路。  
   - 这是做台本结构化时最接近目标的参考之一。  
     参考：[RSTGameTranslation 官网](https://thanhkeke97.github.io/RSTGameTranslation/) / [GitHub](https://github.com/thanhkeke97/RSTGameTranslation)

4. **VideOCR + VideoSubFinder**
   - 借“长视频批处理”和“先找有字帧/清背景再 OCR”的工程模式。  
   - 这是把实时 OCR 思路改造成离线生产线的关键参考。  
     参考：[VideOCR GitHub](https://github.com/timminator/VideOCR) / [VideoSubFinder SourceForge](https://sourceforge.net/projects/videosubfinder/)

### 值得作为增强补充
5. **OwOCR**
   - 借其多输入、多输出和两阶段优化框架。  
     参考：[OwOCR GitHub](https://github.com/AuroraWright/owocr)

6. **visual-novel-game-ocr**
   - 借其“变化关键帧 + 快速离线 OCR 输出”的策略。  
     参考：[visual-novel-game-ocr GitHub](https://github.com/wanghaisheng/visual-novel-game-ocr)

7. **Game2Text**
   - 借脚本匹配增强思路。  
     参考：[Game2Text GitHub](https://github.com/mathewthe2/Game2Text)

---

## 3. 最终判断

如果你希望的是：

- **高质量**
- **可批处理长视频**
- **兼顾无配音文本**
- **成本可控**
- **后续能扩展到多作品**

那么不应寻找“现成一键工具”，而应建设一条：

> **基于社区成熟思路的自定义 OCR-first VN 视频台本抽取流水线**

从工程可行性、质量上限和成本控制三方面看，这是目前最稳妥的路线。

---

## 参考资料

1. Textractor（视频游戏 / VN 文本 Hook）  
   https://github.com/Artikash/Textractor

2. LunaTranslator（HOOK、OCR、模拟器支持）  
   https://docs.lunatranslator.org/en/  
   https://github.com/HIllya51/LunaTranslator

3. GameSentenceMiner OCR 文档（两阶段 OCR）  
   https://docs.gamesentenceminer.com/docs/features/ocr/

4. OwOCR（多输入、多输出、screen capture/OBS/websocket）  
   https://github.com/AuroraWright/owocr

5. Visual Novel OCR Guide（半透明背景、镜像截取、颜色阈值）  
   https://visual-novel-ocr.sourceforge.io/

6. RSTGameTranslation（上下文感知、角色名识别、chat window 优化）  
   https://thanhkeke97.github.io/RSTGameTranslation/  
   https://github.com/thanhkeke97/RSTGameTranslation

7. VideOCR（硬字幕视频 OCR）  
   https://github.com/timminator/VideOCR

8. VideoSubFinder（有字帧检测、清背景文字图）  
   https://sourceforge.net/projects/videosubfinder/

9. visual-novel-game-ocr（关键帧、RapidOCR、txt/SRT/docx）  
   https://github.com/wanghaisheng/visual-novel-game-ocr

10. video-text-extraction（面向 visual novel gameplay video）  
   https://github.com/girubato/video-text-extraction

11. GameDialogueOCR（自定义 ROI 的批量图像 OCR）  
    https://github.com/purpyviolet/GameDialogueOCR

12. Game2Text（OCR-assisted game script matching）  
    https://github.com/mathewthe2/Game2Text

--- Original Design Draft End ---
