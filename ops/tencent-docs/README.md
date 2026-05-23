# 腾讯文档同步手册

本目录只保存项目相关的云文档清单和同步流程，不保存腾讯文档 skill 本体、`mcporter` 本地配置或 token。

## 边界

跟踪到 git：

- `ops/tencent-docs/cloud-docs.json`：云端文件夹和文档 URL 清单。
- `ops/tencent-docs/sync-docs.sh`：从本地 Markdown 创建/替换腾讯文档的辅助脚本。
- 本 README：同步步骤、失败处理和安全约束。

不提交到 git：

- `TENCENT_DOCS_TOKEN`
- `/mnt/c/Users/chuan/.codex/skills/tencent-docs/`
- `mcporter` 本地状态
- 腾讯文档临时上传结果、图片 `image_id` 临时缓存、导入任务日志

## 前置条件

本机需要已安装腾讯文档 skill，并能调用 `mcporter`：

```bash
mcporter list tencent-docs
```

需要设置 token，但不要把 token 写进仓库：

```bash
export TENCENT_DOCS_TOKEN="..."
```

脚本还需要 `jq`、`base64`。

## 云文档清单

当前云文档文件夹：

```text
https://docs.qq.com/desktop/mydoc/folder/dCqgFyBeqUwT
```

具体文档以 `cloud-docs.json` 为准。常用查看命令：

```bash
jq -r '.documents[] | [.key, .title, .url] | @tsv' ops/tencent-docs/cloud-docs.json
mcporter call tencent-docs manage.folder_list --args '{"folder_id":"dCqgFyBeqUwT","start":0}'
```

## 同步普通 Markdown

列出可同步文档：

```bash
ops/tencent-docs/sync-docs.sh --list
```

同步单个文档，默认创建一个新云文档并回写 `cloud-docs.json`：

```bash
ops/tencent-docs/sync-docs.sh --key runtime-readme
ops/tencent-docs/sync-docs.sh --key rt-runbook
```

确认新文档正常后，可以替换旧文档：

```bash
ops/tencent-docs/sync-docs.sh --key runtime-readme --replace
```

`--replace` 的顺序是先创建新文档、插入内容、更新 manifest，最后删除旧文档；不会先删旧文件。

## 同步含图片文档

包含相对图片的 Markdown 不要直接导入。先上传图片，拿到 `image_id` 后替换本地路径，再创建云端版。

当前需要特别处理：

- `ARCHITECTURE-agent-playerbots.md`
- `doc/agent-playerbot-architecture.svg`

操作原则：

1. 用 `tencent-docs.upload_image` 上传图片。
2. 生成云端专用 Markdown，把 `![...](doc/xxx.svg)` 替换成腾讯文档返回的 `image_id`。
3. 创建或替换云文档。
4. 更新 `cloud-docs.json`。

## 常见失败

- `create_smartcanvas_by_mdx` 被 WAF 拦截：改用 `doc` 文档加 `doc.insert_markdown`，本目录脚本默认走这个方式。
- `import_file.sh` 上传 COS 失败：重试；仍失败时使用本脚本的 `doc.insert_markdown` 通路。
- `TENCENT_DOCS_TOKEN` 失效：重新授权后再跑 `mcporter list tencent-docs` 验证。

## 提交流程

改云文档同步相关内容时：

```bash
bash -n ops/tencent-docs/sync-docs.sh
jq empty ops/tencent-docs/cloud-docs.json
git diff --check
git add ops/tencent-docs ops/codex-skills/azerothcore-playerbot-ops README-playerbot-agent-runtime.md
git commit -m "更新腾讯文档同步流程"
git push foxden-app HEAD:playerbot-agent
```

如果更新了 `ops/codex-skills/azerothcore-playerbot-ops/`，还要刷新本机安装态 skill：

```bash
CODEX_HOME_DIR="${CODEX_HOME:-/mnt/c/Users/chuan/.codex}"
mkdir -p "$CODEX_HOME_DIR/skills"
rsync -a --delete ops/codex-skills/azerothcore-playerbot-ops/ "$CODEX_HOME_DIR/skills/azerothcore-playerbot-ops/"
```
