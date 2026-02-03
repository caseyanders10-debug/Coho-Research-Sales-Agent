name: Hotel Intelligence Agent
on:
  workflow_dispatch: # Allows you to run it manually with one click

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - name: Install Dependencies
        run: |
          pip install playwright google-generativeai
          playwright install chromium
      - name: Run Agent
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: python hotel_agent_script.py
      - name: Upload Screenshots
        uses: actions/upload-artifact@v4
        with:
          name: hotel-snapshots
          path: screenshots/*.png
