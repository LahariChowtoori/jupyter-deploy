# === Public TLS certificate (ACM) ===
#
# Public certificate for the deployment's domain, validated via DNS against the
# hosted zone the template already reads in main.tf. This is the certificate the
# NLB TLS listener will present once the router chart terminates public TLS at the
# load balancer instead of in Traefik; the NLB then re-encrypts to Traefik, which
# serves a private-CA certificate the chart mints and owns.
#
# ACM public certificates chain to Amazon Trust Services, so the EKS OIDC identity
# provider and kubelogin keep working when they fetch
# https://<domain>/dex/.well-known/openid-configuration.
#
# No wildcard SAN: every route the router serves (/, /dex, /oauth2, /workspaces/...)
# is path-based on this single host.
resource "aws_acm_certificate" "public" {
  domain_name       = local.full_domain
  validation_method = "DNS"
  tags              = local.combined_tags

  # An ACM certificate has no account-unique name, so a change that forces
  # replacement (e.g. a new subdomain) can safely create the new one first.
  lifecycle {
    create_before_destroy = true
  }
}

# The CNAME(s) ACM looks for to prove domain control. Keyed by domain_name so the
# for_each is stable, and allow_overwrite so a redeploy on the same subdomain
# replaces a stale validation record instead of failing on a conflict.
resource "aws_route53_record" "acm_validation" {
  for_each = {
    for dvo in aws_acm_certificate.public.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  zone_id         = data.aws_route53_zone.domain.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

# Blocks the apply until ACM reports ISSUED. Silently hanging DNS validation is the
# likeliest failure mode here (wrong zone, a records-write the caller cannot make),
# and failing the apply is far cheaper than discovering it when the chart tries to
# attach the certificate to a listener.
#
# The default 75m timeout is cut to 30m, NOT lower: AWS documents that a certificate
# "might continue to display a status of Pending validation for up to 30 minutes" after
# the validation records are written. A shorter deadline turns a slow-but-healthy
# issuance into an apply failure whose output is indistinguishable from a real hang,
# costing a full `jd config && jd up` for a certificate that lands minutes later.
# Refer to: https://docs.aws.amazon.com/acm/latest/userguide/dns-validation.html
resource "aws_acm_certificate_validation" "public" {
  certificate_arn         = aws_acm_certificate.public.arn
  validation_record_fqdns = [for record in aws_route53_record.acm_validation : record.fqdn]

  timeouts {
    create = "30m"
  }
}
