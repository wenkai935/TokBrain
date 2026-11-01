#!/usr/bin/env python3
"""
抖音采集数据 → 飞书多维表格导入工具
====================================
读取抖音采集脚本输出的 JSON 文件，将喜欢、收藏（含收藏夹分类/层级树）、观看历史
批量导入到 obsidian知识库收件箱多维表格的「视频收藏」表中。

功能：
- 自动去重（按视频链接判断，已存在的跳过）
- 批量写入（每批最多 200 条）
- 收藏夹分类自动标记到「来源」和「标签」字段（层级路径，如「收藏-夹A/子夹B」）
- 「收藏夹 / 子收藏夹」独立字段：从采集端"收藏夹路径"拆分父夹/子夹（旧数据回退从标签拆分）
- 支持 dry-run 预览导入结果
- 支持指定只导入某个分类

用法：
  python import_to_bitable.py output/douyin_data_20250101_120000.json
  python import_to_bitable.py data.json --dry-run          # 仅预览，不写入
  python import_to_bitable.py data.json --only like        # 只导入喜欢
  python import_to_bitable.py data.json --only favor       # 只导入收藏
  python import_to_bitable.py data.json --only history     # 只导入观看历史
"""

import json
import sys
import os
import hashlib
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

# ============ 配置 ============
BASE_TOKEN = "WmDJbX7jaaiDvksBX22c7IuLnAf"   # obsidian知识库收件箱
TABLE_ID = "tblgX5UNFSZu31aj"                   # 视频收藏表
BATCH_SIZE = 200                                  # 每批写入条数（API上限200）
# ==============================

# 字段映射：抖音数据字段 → 多维表格字段
# 可根据需要自定义
FIELD_MAPPING = {
    "分享链接": "链接",
    "标题": "标题",
    "作者": "作者",
    "来源": "来源",
    "发布时间": "发布时间",
    "标签": "标签",       # 收藏夹名称或来源类型
    "收藏夹": "收藏夹",     # 父收藏夹（收藏数据专用，如 夹A）
    "子收藏夹": "子收藏夹",  # 子收藏夹/层级路径（如 夹B 或 夹B/夹C）
    # 标签2~标签5 留空，供用户后续手动标注
}


def make_lid(link):
    """生成数据唯一标识 LID：lid_ + sha256(链接) 前 16 位。

    与 bridge_feishu_obsidian.py 的 _make_lid 完全同规则：同一链接无论由
    doubaosave 采集导入还是手机直接分享进表，得到的 LID 一致，可跨系统匹配。
    """
    raw = str(link or "").strip()
    return "lid_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def run_lark_cli(args, input_json=None):
    """执行 lark-cli 命令，返回解析后的 JSON"""
    cmd = ["lark-cli"] + args + ["--as", "user"]
    try:
        result = subprocess.run(
            cmd,
            input=input_json,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            # 尝试解析错误信息
            try:
                err = json.loads(result.stderr)
                return err
            except Exception:
                return {"ok": False, "error": result.stderr[:500]}
        try:
            return json.loads(result.stdout)
        except Exception:
            return {"ok": True, "raw": result.stdout[:500]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "命令执行超时"}
    except FileNotFoundError:
        return {"ok": False, "error": "未找到 lark-cli，请先安装飞书 CLI 工具"}


def get_existing_links():
    """获取表格中已有的视频链接，用于去重"""
    print("[去重] 正在获取表格中已有记录...")
    result = run_lark_cli([
        "base", "+record-list",
        "--base-token", BASE_TOKEN,
        "--table-id", TABLE_ID,
        "--format", "json",
    ])

    existing = set()
    if result.get("ok"):
        data = result.get("data", {})
        # record-list 返回 data.data（数组行格式）+ data.fields（列名）
        fields = data.get("fields", [])
        rows = data.get("data", [])
        if fields and isinstance(rows, list):
            # 找到"链接"列的索引
            try:
                link_idx = fields.index("链接")
            except ValueError:
                link_idx = 0
            for row in rows:
                if isinstance(row, list) and link_idx < len(row):
                    link = row[link_idx]
                    if link:
                        existing.add(str(link).strip())
        # 兼容可能的 records 格式
        elif isinstance(data.get("records"), list):
            for rec in data["records"]:
                fields = rec.get("fields", rec)
                link = fields.get("链接", "")
                if isinstance(link, dict):
                    link = link.get("text", link.get("link", ""))
                if link:
                    existing.add(str(link).strip())

    print(f"[去重] 表格中已有 {len(existing)} 条记录")
    return existing


def _flatten_tree_videos(tree, prefix=""):
    """
    递归展平收藏夹层级树为 [(video_dict, source_label, tag_label), ...]。
    标签带层级路径，如「收藏夹A/子夹B」。
    """
    out = []
    for node in tree:
        if not isinstance(node, dict):
            continue
        path = f"{prefix}/{node['name']}" if prefix else node["name"]
        for v in node.get("videos") or []:
            source = f"收藏-{path}" if path != "默认收藏" else "收藏"
            out.append((v, source, path))
        out.extend(_flatten_tree_videos(node.get("children") or [], path))
    return out


def flatten_videos(data, only=None):
    """
    将采集数据展平为视频列表，每条视频附带来源信息。
    返回: [(video_dict, source_label, tag_label), ...]
    """
    videos = []

    # 喜欢
    if only in (None, "like"):
        liked = data.get("喜欢", [])
        if isinstance(liked, list):
            videos.extend((v, "喜欢", "喜欢") for v in liked)

    # 收藏（优先收藏夹层级树，其次按收藏夹分类的扁平 dict）
    if only in (None, "favor"):
        favorites_tree = data.get("收藏夹结构") or data.get("收藏夹树") or []
        if isinstance(favorites_tree, list) and favorites_tree:
            videos.extend(_flatten_tree_videos(favorites_tree))
        else:
            favorites = data.get("收藏", {})
            if isinstance(favorites, dict):
                for coll_name, vids in favorites.items():
                    if isinstance(vids, list):
                        for v in vids:
                            source = f"收藏-{coll_name}" if coll_name != "默认收藏" else "收藏"
                            videos.append((v, source, coll_name))

    # 观看历史
    if only in (None, "history"):
        history = data.get("观看历史", [])
        if isinstance(history, list):
            videos.extend((v, "观看历史", "观看历史") for v in history)

    return videos


def _split_collection_path(video, tag_label):
    """
    从视频记录中拆出「父收藏夹 / 子收藏夹」。

    优先读采集端新增的"收藏夹路径"字段（如 "夹A/子夹B"）；
    旧数据无该字段时回退从 tag（收藏-夹A/子夹B 或 夹A/子夹B）拆分。
    返回 (parent, child)：child 保留剩余层级路径（如 夹B 或 夹B/夹C）。
    """
    path = str(video.get("收藏夹路径") or "").strip()
    if not path:
        path = str(tag_label or "").strip()
        if path.startswith("收藏-"):
            path = path[len("收藏-"):]
        if path in ("喜欢", "观看历史"):
            path = ""
    parts = [p for p in path.split("/") if p]
    parent = parts[0] if parts else ""
    child = "/".join(parts[1:]) if len(parts) > 1 else ""
    return parent, child


def build_record(video, source_label, tag_label):
    """将抖音视频数据转换为多维表格记录格式"""
    fields = {}

    # 按映射关系填充字段
    for douyin_field, bitable_field in FIELD_MAPPING.items():
        if douyin_field == "来源":
            value = source_label
        elif douyin_field == "标签":
            value = tag_label
        elif douyin_field == "收藏夹":
            value = _split_collection_path(video, tag_label)[0]
        elif douyin_field == "子收藏夹":
            value = _split_collection_path(video, tag_label)[1]
        else:
            value = video.get(douyin_field, "")

        if value is None:
            value = ""
        fields[bitable_field] = str(value).strip()

    # 确保链接字段有值（优先分享链接，回退视频链接）
    if not fields.get("链接"):
        fields["链接"] = str(video.get("视频链接", "")).strip()

    # LID：数据唯一标识（与 bridge 同规则），链接兜底后再生成，保证必基于最终链接
    fields["LID"] = make_lid(fields.get("链接", ""))

    return fields


def batch_create_records(records):
    """批量创建记录，返回 (成功数, 失败数)"""
    if not records:
        return 0, 0

    success = 0
    failed = 0

    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(records) + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"[写入] 第 {batch_num}/{total_batches} 批（{len(batch)} 条）...")

        # 写入当前目录下的临时文件（lark-cli 要求相对路径）
        tmp_path = f"._batch_{batch_num}.json"
        with open(tmp_path, 'w', encoding='utf-8') as tmp:
            json.dump({"create_records": batch}, tmp, ensure_ascii=False)

        try:
            result = run_lark_cli(
                ["base", "+record-batch-create",
                 "--base-token", BASE_TOKEN,
                 "--table-id", TABLE_ID,
                 "--json", f"@{tmp_path}"],
            )
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        if result.get("ok"):
            data = result.get("data", {})
            created = data.get("record_id_list", data.get("records", data.get("created_records", [])))
            count = len(created) if isinstance(created, list) else len(batch)
            success += count
            print(f"[写入] 成功 {count} 条")
        else:
            failed += len(batch)
            err_msg = result.get("error", result.get("message", "未知错误"))
            print(f"[写入] 失败: {err_msg}")

    return success, failed


def main():
    parser = argparse.ArgumentParser(description="抖音采集数据导入飞书多维表格")
    parser.add_argument("input", help="抖音采集脚本输出的 JSON 文件路径")
    parser.add_argument("--only", choices=["like", "favor", "history"],
                        help="只导入指定分类（like=喜欢, favor=收藏, history=观看历史）")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览模式：只统计和展示将导入的内容，不实际写入表格")
    parser.add_argument("--no-dedup", action="store_true",
                        help="关闭去重（默认开启，跳过表格中已存在的链接）")
    args = parser.parse_args()

    # 读取输入文件
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[错误] 文件不存在: {input_path}")
        sys.exit(1)

    print(f"[读取] 正在加载采集数据: {input_path}")
    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    # 展平视频列表
    videos = flatten_videos(data, only=args.only)
    print(f"[统计] 共读取 {len(videos)} 条视频")

    if not videos:
        print("[提示] 没有可导入的视频数据")
        return

    # 去重
    if not args.no_dedup:
        existing_links = get_existing_links()
        new_videos = []
        skipped = 0
        for v, source, tag in videos:
            link = str(v.get("分享链接") or v.get("视频链接") or "").strip()
            if link and link in existing_links:
                skipped += 1
            else:
                new_videos.append((v, source, tag))
                if link:
                    existing_links.add(link)  # 防止本批次内重复
        videos = new_videos
        print(f"[去重] 跳过已存在 {skipped} 条，待导入 {len(videos)} 条")

    # 构建记录
    records = []
    for v, source, tag in videos:
        record = build_record(v, source, tag)
        if record.get("链接"):
            records.append(record)
        else:
            print(f"[跳过] 无链接的视频: {v.get('标题', '未知')[:30]}")

    print(f"[准备] 有效记录 {len(records)} 条")

    # 预览前 5 条
    if records:
        print("\n[预览] 前 5 条记录:")
        print("-" * 60)
        for i, f in enumerate(records[:5]):
            print(f"{i+1}. 来源: {f.get('来源', '')}")
            print(f"   标题: {f.get('标题', '')[:40]}")
            print(f"   作者: {f.get('作者', '')}")
            print(f"   链接: {f.get('链接', '')[:60]}")
            print(f"   标签: {f.get('标签', '')}")
            print(f"   收藏夹: {f.get('收藏夹', '')} / 子收藏夹: {f.get('子收藏夹', '')}")
            print()

    if args.dry_run:
        print("\n[预览模式] 未实际写入表格。去掉 --dry-run 参数即可执行导入。")
        return

    if not records:
        print("[提示] 没有需要导入的记录")
        return

    # 确认导入
    print(f"\n[确认] 即将导入 {len(records)} 条记录到多维表格")
    try:
        confirm = input("确认导入？(y/N): ").strip().lower()
    except EOFError:
        confirm = "y"  # 非交互环境默认确认

    if confirm not in ("y", "yes"):
        print("[取消] 用户取消导入")
        return

    # 执行批量导入
    start_time = datetime.now()
    success, failed = batch_create_records(records)
    elapsed = (datetime.now() - start_time).total_seconds()

    # 汇总
    print("\n" + "=" * 50)
    print("[导入完成] 汇总:")
    print(f"  总计: {len(records)} 条")
    print(f"  成功: {success} 条")
    print(f"  失败: {failed} 条")
    print(f"  耗时: {elapsed:.1f} 秒")
    print(f"\n  多维表格: https://wcntk8teo9g2.feishu.cn/base/{BASE_TOKEN}")


if __name__ == "__main__":
    main()
