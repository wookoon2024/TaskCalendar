# TaskCalendar Browser Bridge

## What it does

- Runs as a Chromium extension for Chrome or Edge.
- Watches every frame, including iframe pages with the same URL.
- Decides whether to inject a button by checking both the URL and DOM selectors.
- Sends the extracted schedule to the local TaskCalendar app on `http://127.0.0.1:18452`.

## Install

1. Start TaskCalendar.
2. Open `chrome://extensions` or `edge://extensions`.
3. Turn on developer mode.
4. Load unpacked extension from this folder:
   - `C:\Pro1\TaskCalendar\browser_extension`
5. Open the extension options page.
6. Paste or edit the JSON config.

## Rule tips

- `urlPatterns`: regular expressions matched against `location.href`
- `requiredSelectors`: every selector here must exist in the current frame
- `requiredTexts`: all of these texts must appear in the frame body text
- `titleSelector`: the element used to read the title
- `buttonContainerSelector`: where the button is appended
- `descriptionSelectors`: texts joined into the calendar description
- `dateSelector` and `timeSelector`: source text for date/time parsing
- `fingerprintSelector`: stable DOM value used to block duplicate imports

## Notes

- The extension only shows a button when the rule matches the current frame.
- If the target site is a single-page app, the MutationObserver will rescan automatically.
- If the button says `실패`, check whether TaskCalendar is running and whether the rule selectors still match the page.
