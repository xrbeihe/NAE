"""Fix corruption and remove double-switchSession bug in app.html"""
path = "D:\\ANE\\frontend\\app.html"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Fix the PowerShell corruption — the `—` and `"` got mangled
fixes = {
    " �": " — ",
    "�": "「",
    "�": "」",
}
# The corruption bytes map to specific characters
# Look for the specific known-corrupted spots:
# Line 593 had the — become corrupted
content = content.replace(" �'", " — '")
content = content.replace(" '", " '")
content = content.replace(" '", " '")

# Remove the inner onclick that duplicates card.onclick
# Old pattern:   onclick="if(!event.target.closest('.del-btn'))switchSession('...')"
# card.innerHTML line with the onclick attribute in it
import re

# The onclick spans across lines, find it in the single card.innerHTML line
# card.innerHTML = '<div style=...  onclick="if(!event.target.closest(\'.del-btn\'))switchSession(\'' + s.session_id + '\')">'
old_line = ''' + '" onclick="if(!event.target.closest(\\'.del-btn\\'))switchSession(\\'' + s.session_id + '\\')">' +'''
new_line = ''' + '">' +'''

content = content.replace(old_line, new_line)

# Fix deleteSession string corruption
content = content.replace("确定要删除世界�? + name + '」吗�?", "确定要删除世界「' + name + '」吗？")
content = content.replace("document.getElementById('session-id-display').textContent = '未创建会�?", "document.getElementById('session-id-display').textContent = '未创建会话'")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("Done")
