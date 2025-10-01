name: MonsterCount

on:
  schedule:
    # Sommerzeit in Berlin entspricht typischerweise 21:55 UTC
    - cron: "55 21 * * *"
    # Winterzeit in Berlin entspricht typischerweise 22:55 UTC
    - cron: "55 22 * * *"
  workflow_dispatch:

jobs:
  run:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      # Vorherigen State laden, falls vorhanden
      - name: Vorherigen State laden
        uses: actions/download-artifact@v4
        with:
          name: state
          path: .
        continue-on-error: true

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run MonsterCount Tracker
        env:
          DISCORD_WEBHOOK: ${{ secrets.DISCORD_WEBHOOK }}
        run: |
          python monstercount_tracker.py

      # Aktuellen State speichern
      - name: State speichern
        uses: actions/upload-artifact@v4
        with:
          name: state
          path: monstercount_state.json
          if-no-files-found: ignore
          retention-days: 90
