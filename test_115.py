import asyncio
from cloudpop.config import get_settings
from cloudpop.providers.provider_115 import get_provider

async def main():
    settings = get_settings()
    p = get_provider("115")
    folder_id = "0"
    if settings.strm.scan_folder_id:
        folder_id = settings.strm.scan_folder_id
    print("Scan folder ID:", folder_id)

    print("\n--- Search using search_videos() ---")
    try:
        count = 0
        async for fi in p.search_videos(folder_id):
            print("Found:", fi.name)
            count += 1
            if count >= 3:
                break
        print("search_videos Total:", count)
    except Exception as e:
        print("Error:", e)
        
    print("\n--- Listing using list_files() ---")
    try:
        count = 0
        async for fi in p.list_files(folder_id):
            print("Found:", fi.name)
            count += 1
            if count >= 3:
                break
        print("list_files Total:", count)
    except Exception as e:
        print("Error:", e)

asyncio.run(main())
