# 会话完成后的文件变更

每轮结束后，如果原生记录包含文件变更，在最终回复下显示汇总卡片：
文件数量、总增删行数、每个文件的路径与增删行数。没有最终回复的失败或中断轮次，
卡片显示在处理过程之后；没有变更时不显示卡片。

点击文件行打开差异浮层，支持行号、代码着色、增加/删除底色、横纵滚动、键盘和 Escape。
“查看变更”打开本轮所有文件的差异。桌面、手机和深色主题共用这些入口。

统计优先使用最新的 `turn/diff/updated` 汇总，表示本轮净变更，同一文件重复修改不重复累加。
缺少汇总的历史记录回退到成功的 `fileChange` 项；此时显示已记录的编辑量。
数据范围为原生运行时报告的修改，不扫描并归属其他进程或 Shell 的未报告变更。

“撤销”经用户点击确认，通过原生 `command/exec` 在会话沙箱中执行，保留聊天记录。
仅支持包含完整 Git blob 版本的文本补丁。执行前校验所有文件仍匹配本轮完成时的版本，
再统一执行反向补丁检查和应用；后续编辑、路径越界、符号链接和不完整版本信息会阻止撤销。
不使用 `git reset`、`checkout` 或对话历史回退。重命名、Windows 和大于 96 KB 的补丁暂不支持撤销。
撤销成功标记保存在当前浏览器；另一浏览器仍会通过内容校验阻止重复撤销。

验证：

```sh
cd frontend
npm test
CONSOLE_BUILD_DIR=/tmp/codex-file-changes-build npm run build
cd ..
CONSOLE_BUILD_DIR=/tmp/codex-file-changes-build PYTHONPATH=src \
  .local/conda/bin/python tests/file_changes_browser_fixture.py 18886
# 另开终端
CONSOLE_TEST_URL=http://127.0.0.1:18886 node tests/test_file_changes_browser.cjs
CONSOLE_TEST_URL=http://127.0.0.1:18886 node tests/test_workbench_browser.cjs
```

可设置 `CHROMIUM_EXECUTABLE` 使用已有浏览器，设置 `CONSOLE_SCREENSHOTS` 保存截图。
撤销测试在临时 Git 仓库验证修改、新增、删除、后续编辑冲突、嵌套工作区和路径拒绝。
浏览器验证完成时机、轮次隔离、刷新、行号、增删统计、弹层、草稿保留、失败提示、
撤销结果、深色和手机布局，不调用模型、不修改用户文件。
