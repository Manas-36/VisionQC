# Contributing

1. Create a branch: `git checkout -b feature/short-name`
2. Set up: `pip install -r requirements-dev.txt`
3. Make your change and add/adjust tests in `tests/`.
4. Run `pytest` - it must pass (CI runs it too).
5. UI changes: run the hub and check the dashboard in a browser.
6. Firmware changes: compile in Arduino IDE (board `XIAO_ESP32S3`, PSRAM `OPI PSRAM`). Never commit `secrets.h`.
7. Open a pull request using the template.

Commit style: short imperative subject ("Add auto-test interval to dashboard").
