# medical-web-server

- Primary entry: https://medical.newapi.com.cn/
- Runtime: `medical-exam.service`, port 8080
- Application: `/srv/projects/medical-web-server/app`
- Knowledge base: `/srv/projects/medical-web-server/knowledge-base`
- Environment: `/etc/medical-exam.env` (mode 600)
- Canonical root only: no host compatibility symlinks or alias project paths.
- Health: `/usr/local/sbin/projectsctl health medical-web-server`
- Update: requires an explicit release tar.gz artifact
