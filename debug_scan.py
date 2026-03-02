#!/usr/bin/env python3
"""调试扫描视频问题的脚本"""

import asyncio
import logging
import sys

# 设置详细日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 添加项目路径
sys.path.insert(0, '/Users/linglin/projects/CloudPop')

from cloudpop.config import get_settings
from cloudpop.providers.provider_115 import get_provider


async def test_list_files(folder_id: str = "0"):
    """测试列出文件夹内容（非递归）"""
    logger.info(f"=== 测试 list_files (folder_id={folder_id}) ===")

    try:
        provider = get_provider("115")
    except Exception as e:
        logger.error(f"获取 provider 失败: {e}")
        return

    try:
        count = 0
        files = []
        dirs = []

        async for fi in provider.list_files(folder_id):
            count += 1
            if fi.is_dir:
                dirs.append(fi)
            else:
                files.append(fi)

        logger.info(f"总共 {count} 个条目: {len(dirs)} 个目录, {len(files)} 个文件")

        # 显示前5个视频文件
        video_exts = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.m4v', '.ts', '.m2ts'}
        videos = [f for f in files if any(f.name.lower().endswith(ext) for ext in video_exts)]

        if videos:
            logger.info(f"找到 {len(videos)} 个视频文件（通过扩展名判断）:")
            for v in videos[:5]:
                logger.info(f"  - {v.name} (id={v.id}, pickcode={v.pickcode})")
        else:
            logger.warning("没有找到视频文件（通过扩展名判断）")
            logger.info("普通文件示例:")
            for f in files[:5]:
                logger.info(f"  - {f.name} (id={f.id})")

    except Exception as e:
        logger.error(f"list_files 出错: {e}", exc_info=True)


async def test_search_videos(folder_id: str = "0"):
    """测试搜索视频功能（使用 type=4）"""
    logger.info(f"=== 测试 search_videos (folder_id={folder_id}, type=4) ===")

    try:
        provider = get_provider("115")
    except Exception as e:
        logger.error(f"获取 provider 失败: {e}")
        return

    try:
        count = 0
        async for fi in provider.search_videos(folder_id):
            count += 1
            if count <= 5:
                logger.info(f"  视频 #{count}: {fi.name} (id={fi.id}, pickcode={fi.pickcode})")

        if count == 0:
            logger.warning("search_videos 没有找到任何视频文件")
            logger.warning("可能原因:")
            logger.warning("  1. 该目录下确实没有视频文件")
            logger.warning("  2. 115 尚未对该目录建立媒体库索引（需要手动在 115 网页版点击'视频'分类触发）")
            logger.warning("  3. 该目录没有被 115 识别为媒体文件夹")
        else:
            logger.info(f"总共找到 {count} 个视频文件")

    except Exception as e:
        logger.error(f"search_videos 出错: {e}", exc_info=True)


async def test_raw_api(folder_id: str = "0"):
    """测试原始 API 调用，查看返回的数据结构"""
    logger.info(f"=== 测试原始 API 调用 (folder_id={folder_id}) ===")

    import httpx
    from cloudpop.config import get_settings

    settings = get_settings()
    if not settings.is_115_configured():
        logger.error("115 未配置，请先设置 cookies")
        return

    c = settings.provider_115.cookies
    cookies = {"UID": c.UID, "CID": c.CID, "SEID": c.SEID}
    if c.KID:
        cookies["KID"] = c.KID

    # 测试 files API
    url = "https://webapi.115.com/files"
    params = {
        "aid": 1,
        "cid": folder_id,
        "o": "file_name",
        "asc": 1,
        "offset": 0,
        "limit": 100,
        "show_dir": 1,
        "natsort": 1,
        "format": "json",
    }

    headers = {
        "User-Agent": settings.provider_115.user_agent,
        "Referer": "https://115.com/",
        "Accept": "application/json, text/plain, */*",
    }

    async with httpx.AsyncClient(cookies=cookies, headers=headers, follow_redirects=True) as client:
        try:
            resp = await client.get(url, params=params)
            logger.info(f"files API 状态码: {resp.status_code}")

            data = resp.json()
            logger.info(f"API 返回结构: {list(data.keys())}")

            if "data" in data:
                items = data["data"]
                logger.info(f"返回条目数: {len(items)}")
                if items:
                    logger.info(f"第一条记录示例: {items[0]}")

                    # 检查是否有视频文件
                    video_exts = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.m4v', '.ts', '.m2ts'}
                    videos = [item for item in items if any(item.get('n', '').lower().endswith(ext) for ext in video_exts)]
                    if videos:
                        logger.info(f"找到 {len(videos)} 个视频文件（通过扩展名判断）")
                        for v in videos[:3]:
                            logger.info(f"  - {v.get('n')} (fid={v.get('fid')}, pc={v.get('pc')})")

            if "count" in data:
                logger.info(f"API 报告总数 count: {data['count']}")
            if "path" in data:
                logger.info(f"路径信息: {data['path']}")

        except Exception as e:
            logger.error(f"API 调用出错: {e}", exc_info=True)

    # 测试 search API
    logger.info("\n--- 测试 search API (type=4) ---")
    url = "https://webapi.115.com/files/search"
    params = {
        "cid": folder_id,
        "type": 4,
        "limit": 100,
        "offset": 0,
        "format": "json",
        "natsort": 1,
    }

    async with httpx.AsyncClient(cookies=cookies, headers=headers, follow_redirects=True) as client:
        try:
            resp = await client.get(url, params=params)
            logger.info(f"search API 状态码: {resp.status_code}")

            data = resp.json()
            logger.info(f"API 返回结构: {list(data.keys())}")

            if "data" in data:
                items = data["data"]
                logger.info(f"返回条目数: {len(items)}")
                if items:
                    for item in items[:3]:
                        logger.info(f"  - {item.get('n')} (fid={item.get('fid')}, pc={item.get('pc')})")
                else:
                    logger.warning("search API 返回空列表 - 这可能是因为 115 尚未对该目录建立媒体库索引")

            if "count" in data:
                logger.info(f"API 报告总数 count: {data['count']}")

        except Exception as e:
            logger.error(f"search API 调用出错: {e}", exc_info=True)


async def main():
    import argparse

    parser = argparse.ArgumentParser(description='调试 115 扫描功能')
    parser.add_argument('folder_id', nargs='?', default='0', help='要测试的文件夹 ID (默认: 0 根目录)')
    args = parser.parse_args()

    folder_id = args.folder_id

    logger.info(f"开始测试，folder_id={folder_id}")
    logger.info("=" * 60)

    # 1. 测试原始 API
    await test_raw_api(folder_id)

    logger.info("\n" + "=" * 60 + "\n")

    # 2. 测试 list_files
    await test_list_files(folder_id)

    logger.info("\n" + "=" * 60 + "\n")

    # 3. 测试 search_videos
    await test_search_videos(folder_id)

    logger.info("\n" + "=" * 60)
    logger.info("测试完成")


if __name__ == "__main__":
    asyncio.run(main())
