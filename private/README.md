# private/ —— 个人隐私目录（永不提交 Git）

本目录已写入根 `.gitignore`（`/private/`），里面的任何内容都不会被提交、不会出现在 GitHub 上。

## 放什么

- 密钥、token、Cookie、账号密码（例如带认证的代理地址 `http://user:pass@host:port`，本行仅为格式示例）<!-- privacy-scan: ignore -->
- 个人测试配置、私密笔记、不打算公开的账号信息
- 任何含手机号、私人邮箱、身份证号等个人信息的文件

## 怎么用

直接把文件丢进来即可。代码需要读取时，用路径 `private/xxx` 引用，但**不要**把读取到的值硬编码进会提交的源码；
从环境变量或本目录文件读取，并在代码里给出缺失时的安全默认值。

## 两个特殊文件（不在本目录，同样不会上传）

以下两个文件由程序运行时**自动生成在 `core/` 目录**（路径写死在代码里，移走会被程序重建），
已被根 `.gitignore` 单独忽略，保持原位即可：

- `core/jm_option.yml` —— 下载源/代理/并发等本机配置（若在代理里填了账号密码，它就是敏感文件）
- `core/comics.db` —— 个人书架、阅读历史、收藏数据库

## 双重保险

即使误把敏感内容写进了被跟踪的文件，提交时 `tools/privacy_scan.py`（pre-commit 钩子）会扫描
暂存区，发现手机号 / 密钥 / 私钥 / 凭据 URL 等模式会**阻断提交**并打印文件行号。

安装钩子（换机或克隆后执行一次）：

```powershell
.venv-build\Scripts\python.exe tools\install_hooks.py
```

手动全量自查：

```powershell
.venv-build\Scripts\python.exe tools\privacy_scan.py --head       # 查整个仓库
.venv-build\Scripts\python.exe tools\privacy_scan.py --zip 包路径  # 查待发布的便携 zip
```
