# AWS EC2 JupyterLab Template

The **AWS EC2 JupyterLab Template** deploys a **single-user JupyterLab** application to a dedicated
Amazon EC2 instance, reached from your laptop through a local client proxy over a pinned
self-signed TLS connection, authorized by your AWS identity.

**AWS credentials are the only prerequisite**: no domain, no Route 53 hosted zone, no ACM
certificate, and no GitHub OAuth app. The instance has no public URL and no Elastic IP.

The **AWS EC2 JupyterLab Template** is maintained and supported by AWS.

## 10k View

When you run `jd open`, `jupyter-deploy` starts a local proxy on your laptop and opens your web
browser to a loopback address (for example `http://127.0.0.1:PORT/lab`). The browser talks plain
HTTP to the local proxy; the proxy forwards each request to the instance's Traefik on port 443 over
a pinned self-signed TLS connection and injects a short-lived AWS-identity (STS) token. An
auth sidecar on the instance validates the token against an allowlist of IAM role and user names,
then Traefik forwards the request to the `jupyter` container. There is no browser sign-in.

```
jd init . -E terraform -P aws -I ec2 -T jupyterlab   # scaffold the project
jd config                                            # region, instance type, volume size
jd up                                                # provision instance + self-signed cert
jd open                                              # start the proxy and open the browser
```

## Next Steps

```{toctree}
:maxdepth: 2

prerequisites
user-guide
architecture
details
```

## License

Licensed under the [MIT License](https://github.com/jupyter-infra/jupyter-deploy/blob/main/libs/jupyter-deploy-tf-aws-ec2-jupyterlab/LICENSE).
