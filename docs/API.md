# JMComicReader HTTP API 参考

> 适用于桌面版 `core/app.py` 暴露的 Flask 路由，也作为手机端（Android / 其他平台）复用或对接的契约。
> 所有 JSON 接口统一返回：`{success: bool, data?: any, message?: string, ...}`。

## 页面路由（桌面 WebView 用）

| 路由 | 说明 |
|------|------|
| `GET /` | 重定向到 `/search` |
| `GET /search` | 搜索页 |
| `GET /library` | 书架/本地漫画页 |
| `GET /detail/<jm_id>` | 漫画详情页 |
| `GET /reader/<jm_id>` | 阅读页 |
| `GET /settings` | 设置页 |
| `GET /downloads` | 下载管理页 |

## 搜索

### `GET /api/search/jm/<jm_id>`
按 JM 号查详情。
- **响应**：`{success, data: ComicInfo}`

### `GET /api/search/keyword?keyword=&sort=desc&order_by=mr&time=a&page=1`
站内关键词搜索。
- `sort`: `asc` / `desc`（收藏数排序）
- `order_by`: `mr|mv|mp|tr`
- `time`: `a|t|w|m`
- **响应**：`{success, data: [ComicInfo]}`

### `GET /api/search/tags?tags=a,b&mode=or`
按标签搜索（自动展开同义词）。
- `mode`: `or`（默认）/ `and`
- **响应**：`{success, data: [ComicInfo], stats: {tag: count, _total: count}}`

### `GET /api/search/author?author=xxx`
按作者搜索（自动展开同义署名）。
- **响应**：`{success, data: [ComicInfo], author}`

### `GET /api/search/combined?keyword=&author=&tags=a,b&mode=or`
联合搜索：关键词 / 作者 / 标签取交集。
- **响应**：`{success, data: [ComicInfo], keyword, author, tags, mode}`

### `GET /api/search/enrich?ids=1,2,3`
批量补全漫画详情（最多 12 个）。
- **响应**：`{success, data: {jm_id: ComicInfo}}`

## 封面

### `GET /api/cover/<jm_id>`
在线漫画封面。若已下载则返回本地封面文件；否则返回 `{data: {cover: url, cover_local: path}}`。

### `GET /api/cover/downloaded/<jm_id>`
已下载漫画的本地封面文件。

## 下载

### `POST /api/download/<jm_id>`
下载单本漫画。
- **Body**：`{category_ids?: [int]}`
- **响应**：`{success, download_id, message}` / `{success:false, downloaded:true}`

### `POST /api/download/batch`
批量下载。
- **Body**：`{ids: [int], category_ids?: [int]}`
- **响应**：`{success, data: [{jm_id, status, download_id|message}]}`

### `GET /api/download/progress/<download_id>`
单个任务进度：`{data: {progress, status, message, jm_id, title}}`。

### `DELETE /api/download/progress/<download_id>`
从内存中的"进行中"列表移除（失败/卡顿时清理）。

### `GET /api/download/progress`
所有进行中任务：`{data: {download_id: Progress}}`。

### `GET /api/download/history`
下载历史（最近 50 条），每条带 `files_exist`。

### `DELETE /api/download/history/<record_id>`
删除单条历史记录。

### `DELETE /api/download/history`
清空全部历史记录。

## 分类

### `GET /api/categories?tree=0`
分类列表。`tree=1` 返回树形结构。

### `POST /api/categories`
创建分类。
- **Body**：`{name, parent_id?}`

### `PUT /api/categories/<cat_id>`
修改分类。
- **Body**：`{name?, parent_id?}`

### `DELETE /api/categories/<cat_id>`
删除分类。

### `POST /api/categories/<cat_id>/move`
移动分类（重定父级）。
- **Body**：`{parent_id: int|null}`

### `GET /api/comic/<jm_id>/categories`
获取漫画所属分类。

### `PUT /api/comic/<jm_id>/categories`
设置漫画所属分类。
- **Body**：`{category_ids: [int]}`

## 已下载 / 阅读

### `GET /api/downloaded?category_id=`
本地漫画列表，可按分类筛选。每条带 `blocked` 字段（是否被拉黑）。

### `GET /api/read/<jm_id>`
阅读初始化：返回章节列表与第一章页码。

### `GET /api/read/<jm_id>/chapter/<chapter_id>`
切换章节数据。

### `GET /api/comic/<jm_id>/page/<page_num>?chapter=xxx`
返回单页图片文件流。

### `DELETE /api/delete/<jm_id>`
删除本地漫画。

## 缓存

### `GET /api/cache/status`
缓存大小：`{data: {cache_size, cache_size_mb, need_cleanup}}`。

### `POST /api/cache/clear`
清理缓存，保护已下载漫画封面。返回清理字节数与剩余大小。

## 设置

### `GET /api/settings`
所有系统配置项：`{data: {max_concurrent_downloads, auto_cleanup_cache, cache_size_limit, image_quality, theme, proxy_enabled, proxy_url, search_result_limit, search_priority, hide_page_chapter, show_block_hits, search_tag_limit}}`。

### `POST /api/settings`
保存任意配置键值对。
- **Body**：`{key: value}`

## 拉黑

### `GET /api/blocklist`
列表：`{data: [{id, block_type, value, note, create_time}]}`。

### `POST /api/blocklist`
添加拉黑项。
- **Body**：`{type: "author"|"work", value, note?}`
- **响应**：`{success, data: {id}, local_hits: [jm_id]}`

### `DELETE /api/blocklist/<bid>`
按 id 删除。

### `DELETE /api/blocklist/by-value`
按值删除。
- **Body**：`{type, value}`

### `GET /api/blocklist/affects?jm_id=&author=`
返回影响指定本地漫画的拉黑记录 id 列表。

## 别名 / 同义词

### `GET /api/aliases?type=`
别名列表。`type`: `tag` / `author`。

### `POST /api/aliases`
添加别名。
- **Body**：`{type, alias, canonical}`

### `DELETE /api/aliases/<aid>`
删除别名。

### `GET /api/aliases/suggestions`
同义词建议（内置种子 + 本地共现挖掘）。

## 数据模型约定

### ComicInfo
```json
{
  "id": 123,
  "title": "标题",
  "author": "作者",
  "cover": "封面 URL",
  "favorites": "1.2k",
  "tags": ["标签1", "标签2"],
  "description": "简介..."
}
```

### Progress
```json
{
  "progress": 45,
  "status": "starting|downloading|completed|error",
  "message": "当前状态文本",
  "jm_id": 123,
  "title": "标题"
}
```
