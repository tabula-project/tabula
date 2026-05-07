#!/usr/bin/env bash
#
# migrate-from-loom.sh — move pi-crash-logger, R2 backup, and per-project handoff
# state out of ~/.loom/, then delete the OAuth-pool components.
#
# Idempotent. Safe to re-run. Run on each machine in the fleet that has
# ~/.loom/ installed.
#
# Done on the sovereign Mac Studio on 2026-05-04. This script captures the
# same operations for re-application elsewhere.
#
# Usage: bash migrate-from-loom.sh [--dry-run]

set -euo pipefail

DRY=false
[[ "${1:-}" == "--dry-run" ]] && DRY=true

run() {
    echo "+ $*"
    $DRY || "$@"
}

if [[ ! -d "$HOME/.loom" ]]; then
    echo "no ~/.loom — nothing to migrate. exiting."
    exit 0
fi

echo "=== STEP 0: snapshot ~/.loom for safety ==="
run tar czf "/tmp/loom-pre-removal-snapshot-$(hostname -s).tar.gz" -C "$HOME" .loom

echo ""
echo "=== STEP 1: relocate pi-crash-logger.js → ~/.local/lib/ ==="
if [[ -f "$HOME/.loom/bin/pi-crash-logger.js" ]]; then
    run mkdir -p "$HOME/.local/lib" "$HOME/.local/share/pi-crash-logs"
    # rewrite hardcoded log path in the JS file as we copy
    if ! $DRY; then
        sed 's|path.join(os.homedir(), .\.loom., .log.)|path.join(os.homedir(), ".local", "share", "pi-crash-logs")|g' \
            "$HOME/.loom/bin/pi-crash-logger.js" > "$HOME/.local/lib/pi-crash-logger.js"
        sed -i '' 's|~/.loom/log/pi-crash|~/.local/share/pi-crash-logs/pi-crash|g' "$HOME/.local/lib/pi-crash-logger.js"
        chmod 644 "$HOME/.local/lib/pi-crash-logger.js"
    fi
fi

echo ""
echo "=== STEP 2: relocate R2 backup → ~/.local/share/r2-backup/ ==="
if [[ -d "$HOME/.loom/backup" ]]; then
    run mkdir -p "$HOME/.local/share/r2-backup/snapshots" "$HOME/.local/share/r2-backup/log"
    if ! $DRY; then
        cp -p "$HOME/.loom/backup/"*.sh "$HOME/.loom/backup/"*.txt "$HOME/.loom/backup/env.sh" \
            "$HOME/.local/share/r2-backup/" 2>/dev/null || true
        cp -p "$HOME/.loom/backup/snapshots/"* "$HOME/.local/share/r2-backup/snapshots/" 2>/dev/null || true

        # Rewrite scripts: LOOM_HOME → BACKUP_HOME
        for f in "$HOME/.local/share/r2-backup/backup.sh" \
                 "$HOME/.local/share/r2-backup/check.sh" \
                 "$HOME/.local/share/r2-backup/restore.sh"; do
            [[ -f "$f" ]] || continue
            sed -i '' 's|LOOM_HOME="${LOOM_HOME:-$HOME/\.loom}"|BACKUP_HOME="${BACKUP_HOME:-$HOME/.local/share/r2-backup}"|g' "$f"
            sed -i '' 's|\$LOOM_HOME/backup|$BACKUP_HOME|g' "$f"
            sed -i '' 's|\$LOOM_HOME/log|$BACKUP_HOME/log|g' "$f"
            sed -i '' 's|\$LOOM_HOME|$BACKUP_HOME|g' "$f"
            sed -i '' 's|loom-backup|r2-backup|g' "$f"
            sed -i '' 's|~/.loom/log/check.log|~/.local/share/r2-backup/log/check.log|g' "$f"
        done

        # sources.txt: drop ~/.loom path, add new backup home, rename launchagent paths, drop loom.rank
        sed -i '' 's|^/Users/[^/]*/\.loom$|'"$HOME"'/.local/share/r2-backup|' "$HOME/.local/share/r2-backup/sources.txt"
        sed -i '' 's|io\.loom\.backup\.plist|io.r2-backup.hourly.plist|g' "$HOME/.local/share/r2-backup/sources.txt"
        sed -i '' 's|io\.loom\.backup-check\.plist|io.r2-backup.check.plist|g' "$HOME/.local/share/r2-backup/sources.txt"
        sed -i '' '/io\.loom\.rank\.plist/d' "$HOME/.local/share/r2-backup/sources.txt"
        sed -i '' 's|^# Loom:.*|# Backup tool config (sources.txt, scripts, env, restic snapshot dir)|' "$HOME/.local/share/r2-backup/sources.txt"
    fi

    # Generate new LaunchAgents
    if ! $DRY; then
        cat > "$HOME/Library/LaunchAgents/io.r2-backup.hourly.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>io.r2-backup.hourly</string>
<key>ProgramArguments</key><array>
<string>/bin/bash</string><string>-lc</string>
<string>$HOME/.local/share/r2-backup/backup.sh --tag hourly &gt;&gt; $HOME/.local/share/r2-backup/log/backup.launchd.log 2&gt;&amp;1</string>
</array>
<key>StartInterval</key><integer>3600</integer>
<key>RunAtLoad</key><false/>
<key>ThrottleInterval</key><integer>1800</integer>
<key>StandardOutPath</key><string>$HOME/.local/share/r2-backup/log/backup.stdout.log</string>
<key>StandardErrorPath</key><string>$HOME/.local/share/r2-backup/log/backup.stderr.log</string>
<key>EnvironmentVariables</key><dict>
<key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
<key>HOME</key><string>$HOME</string>
</dict></dict></plist>
PLIST

        cat > "$HOME/Library/LaunchAgents/io.r2-backup.check.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>io.r2-backup.check</string>
<key>ProgramArguments</key><array>
<string>/bin/bash</string><string>-lc</string>
<string>$HOME/.local/share/r2-backup/check.sh &gt;&gt; $HOME/.local/share/r2-backup/log/check.launchd.log 2&gt;&amp;1</string>
</array>
<key>StartCalendarInterval</key><dict>
<key>Weekday</key><integer>6</integer>
<key>Hour</key><integer>4</integer>
<key>Minute</key><integer>0</integer>
</dict>
<key>StandardOutPath</key><string>$HOME/.local/share/r2-backup/log/check.stdout.log</string>
<key>StandardErrorPath</key><string>$HOME/.local/share/r2-backup/log/check.stderr.log</string>
<key>EnvironmentVariables</key><dict>
<key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
<key>HOME</key><string>$HOME</string>
</dict></dict></plist>
PLIST
    fi
fi

echo ""
echo "=== STEP 3: relocate handoff state → ~/.local/share/handoff/ ==="
if [[ -d "$HOME/.loom/handoff" ]]; then
    run mkdir -p "$HOME/.local/share/handoff"
    run cp -p "$HOME/.loom/handoff/"*.md "$HOME/.local/share/handoff/" 2>/dev/null || true
fi

echo ""
echo "=== STEP 4: update Pi extensions to point at new handoff path ==="
HANDOFF_TS="$HOME/.pi/agent/extensions/handoff/index.ts"
GEN_HANDOFFS_CJS="$HOME/.pi/agent/extensions/memory/gen-handoffs.cjs"
if ! $DRY; then
    if [[ -f "$HANDOFF_TS" ]]; then
        sed -i '' 's|~/.loom/handoff/<project>.md|~/.local/share/handoff/<project>.md|g' "$HANDOFF_TS"
        # Replace LOOM_HOME / HANDOFF_DIR pair with single HANDOFF_DIR pointing at new path.
        # Match the two-line pattern (this is the brittle part — if upstream changes, hand-edit).
        perl -i -0pe 's|const LOOM_HOME = process\.env\.LOOM_HOME \|\| join\(homedir\(\), "\.loom"\);\nconst HANDOFF_DIR = join\(LOOM_HOME, "handoff"\);|const HANDOFF_DIR = process.env.HANDOFF_DIR || join(homedir(), ".local", "share", "handoff");|' "$HANDOFF_TS"
    fi
    if [[ -f "$GEN_HANDOFFS_CJS" ]]; then
        sed -i '' 's|const HANDOFF_DIR = path.join(os.homedir(), "\.loom", "handoff");|const HANDOFF_DIR = process.env.HANDOFF_DIR || path.join(os.homedir(), ".local", "share", "handoff");|' "$GEN_HANDOFFS_CJS"
        sed -i '' 's|~/.loom/handoff/\${project}\.md|\${HANDOFF_DIR}/\${project}.md|g' "$GEN_HANDOFFS_CJS"
    fi
fi

echo ""
echo "=== STEP 5: update ~/.zshrc to point pi-crash-logger at new path ==="
if grep -q "\.loom/bin/pi-crash-logger.js" "$HOME/.zshrc" 2>/dev/null; then
    if ! $DRY; then
        sed -i '' 's|\$HOME/.loom/bin/pi-crash-logger.js|$HOME/.local/lib/pi-crash-logger.js|g' "$HOME/.zshrc"
        sed -i '' 's|"$HOME/.loom/log/pi-crash-|"$HOME/.local/share/pi-crash-logs/pi-crash-|g' "$HOME/.zshrc"
        sed -i '' 's|ls -t $HOME/.loom/log/pi-crash|ls -t $HOME/.local/share/pi-crash-logs/pi-crash|g' "$HOME/.zshrc"
        sed -i '' 's|no crash logs in ~/.loom/log/|no crash logs in ~/.local/share/pi-crash-logs/|g' "$HOME/.zshrc"
    fi
fi

echo ""
echo "=== STEP 6: clear gt agent overrides that point at loom wrappers ==="
GT_CONFIG="$HOME/gt/settings/config.json"
if [[ -f "$GT_CONFIG" ]] && grep -q "\.loom/bin" "$GT_CONFIG"; then
    if ! $DRY; then
        # Replace agents block with empty {}
        jq '.agents = {}' "$GT_CONFIG" > "$GT_CONFIG.new" && mv "$GT_CONFIG.new" "$GT_CONFIG"
    fi
fi

echo ""
echo "=== STEP 7: swap LaunchAgents (unload old, load new) ==="
run launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.loom.backup.plist" 2>/dev/null || true
run launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.loom.backup-check.plist" 2>/dev/null || true
run launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.loom.rank.plist" 2>/dev/null || true
run launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.r2-backup.hourly.plist" 2>/dev/null || true
run launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.r2-backup.check.plist" 2>/dev/null || true
run rm -f "$HOME/Library/LaunchAgents/io.loom.backup.plist" "$HOME/Library/LaunchAgents/io.loom.backup-check.plist" "$HOME/Library/LaunchAgents/io.loom.rank.plist"

echo ""
echo "=== STEP 8: verify new backup runs at new location ==="
if [[ -x "$HOME/.local/share/r2-backup/backup.sh" ]]; then
    run env BACKUP_HOME="$HOME/.local/share/r2-backup" \
        bash "$HOME/.local/share/r2-backup/backup.sh" --tag verify-relocation
fi

echo ""
echo "=== STEP 9: nuke ~/.loom and uninstall Pi loom extension ==="
run rm -f "$HOME/.local/bin/loom"
run rm -rf "$HOME/.pi/agent/extensions/loom"
run rm -rf "$HOME/.loom"

echo ""
echo "=== DONE. ==="
echo "Snapshot at /tmp/loom-pre-removal-snapshot-$(hostname -s).tar.gz"
echo "If anything is broken, restore with:"
echo "  tar xzf /tmp/loom-pre-removal-snapshot-$(hostname -s).tar.gz -C \$HOME"
