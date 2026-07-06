#!/bin/bash
# git_worktree_check.sh — git 工作樹健康檢查(W3,審計 2026-07-07)
#
# 四個排程(19:00 主跑、08:30 macro、05:30 us_refresh、週六 weekly)互相 push 同一
# branch,靠 pull --rebase --autostash 硬扛。撞車時 rebase-in-progress 殘留會讓
# 之後每一輪 git 操作全掛,直到人工介入——本檢查在 run 開頭抓出殘留,明確告警。
#
# exit 0 = 乾淨;exit 1 = 有 rebase/merge/cherry-pick 殘留(caller 告警並中止 publish)。

GITDIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0   # 非 git repo → 視為無事
for f in rebase-merge rebase-apply MERGE_HEAD CHERRY_PICK_HEAD; do
    if [ -e "$GITDIR/$f" ]; then
        echo "❌ git 工作樹卡在 ${f}(前次 rebase/merge 未完成)——請人工處理:"
        echo "   git rebase --abort   # 或 git merge --abort / 檢視後 --continue"
        exit 1
    fi
done
exit 0
