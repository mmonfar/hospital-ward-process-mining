# Product brief: Clinician walking distance

Part of the Hospital ward process mining model. Status: built, not public. Synthetic data only. Not a medical device.

## Who it is for
Ward managers, clinical operations leads and rota planners who ask why ward rounds feel scattered.

## The question it answers
How far, and for how long, do clinicians walk between wards on a round, and could a different order or a joint round save that walk?

## The one number it shows
Total walking distance, in metres, with the matching motion time, in minutes. The sample set gives 278 m and 4 min for one clinician.
(Other figures sit next to it: wards visited, floor changes.)

## How to use it (30 seconds)
Open the page. Press "Load sample data". Press "Build timeline". Press play.

## What is still missing before it can go public
1. A clinician check of the discovered process (graph node N16). Not done. Nothing here is checked against a real clinician.
2. A first run on a user's own data, after information-governance approval (N17). Not done, by design.
3. The owner's decision on showcase tier (below) and the owner's OK on the post text (post 26 Jan 2027, draft).
4. Security scan tools (ruff, bandit, gitleaks) are not installed here, so the release check shows them as unknown.
5. Short video for the post exists (marketing/); it needs the usual quality gate.
6. Remote state: a public repo (mmonfar/hospital-ward-process-mining) exists and local is level with it. Any push needs the owner.

## Showcase tier: recommendation
Recommend **video-only first, live demo second**.
- The viewer loads three.js and fonts from the internet (unpkg, Google Fonts). A live page would call third parties and needs the liability gate (R026).
- The numbers come from a small synthetic set (19 events, 2 clinicians). A video can frame them as an example; a live page invites people to try their own CSV, and a real file raises data-protection questions.
- The post on 26 Jan 2027 already uses a video. One asset, one story.
- A live demo is cheap later: the page is static, works on a phone, and has the synthetic and not-a-medical-device lines on screen. Do it after the clinician check (N16), with the sample data only and the CSV upload clearly marked "stays in your browser" (confirm this first).

## Website card line
**Clinician walking distance**: a ward viewer that shows how far clinicians walk between wards, on synthetic data.
