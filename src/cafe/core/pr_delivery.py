"""Capability-owned publication of an already resolved delivery endpoint."""

from pathlib import Path
from cafe.core.git_delivery import (
    git_text as _git, bind_publication_request, require_publication_target,
)
from cafe.core.packet_io import canonical_json, atomic_write_bytes


def publish_resolved_pr(issue_dir: Path, root: Path, output: Path, *, context, before_dispatch, registry=None, approval_task_id=None, correlation_id=None) -> dict:
    """Publish through existing capability policy; persist attempts before external work."""
    from cafe.core.capabilities import load_capability_registry, default_capability_definition_dirs, run_capability_request
    import json
    import subprocess

    def action():
        before_dispatch()
        endpoint = context["delivery_endpoint"]
        if endpoint["route"] != "pr":
            raise ValueError("confirmed delivery is not PR publication")
        target = output.resolve().relative_to(root.resolve()).as_posix()
        if not output.resolve().is_relative_to(issue_dir.resolve()) or output.is_symlink():
            raise ValueError("PR material must belong to this issue")
        request = {"capability": "cafe.pr.publish", "args": {"output": target,
            "base": endpoint["target_branch"], "remote": endpoint["remote"]},
            "effects": {"writes": [target, ".git", issue_dir.relative_to(root).as_posix()],
                "network_destinations": ["github.com", "api.github.com"], "browser_open": []},
            "credentials": ["gh"], "permissions": {
                "writes": [target, ".git", issue_dir.relative_to(root).as_posix()],
                "network": ["github.com", "api.github.com"]}}
        request = bind_publication_request(context, request)
        path = issue_dir / "delivery_result.json"
        if path.exists():
            raise ValueError("PR delivery already attempted; reconcile read-only instead of replaying")
        from cafe.core.capabilities import evaluate_capability_request, PolicyDecision
        definitions = registry if registry is not None else load_capability_registry(default_capability_definition_dirs(root))
        evaluation = evaluate_capability_request(definitions, request)
        if evaluation.decision == PolicyDecision.REQUIRE_APPROVAL and approval_task_id is None:
            from cafe.core.capability_approvals import CapabilityApprovalService
            service = CapabilityApprovalService(issue_dir=issue_dir,
                workflow_id=context["identity"]["workflow_id"], step="delivery", iteration=1)
            task = service.request_approval(request=evaluation.request, manifest=evaluation.manifest)
            return {"delivered": False, "needs_human_task": True, "task_id": task.id,
                    "correlation_id": task.capability_approval["correlation_id"]}
        if evaluation.decision == PolicyDecision.DENY:
            run = run_capability_request(repo_root=root, registry=definitions,
                capability_request=request, output_file=output, timeout_sec=240)
            return {"delivered": False, "receipt": run.receipt,
                    "needs_human_task": evaluation.decision == PolicyDecision.REQUIRE_APPROVAL}
        repository = None
        def dispatch_check():
            nonlocal repository
            before_dispatch()
            # Credential-bearing repository lookup follows the capability gate.
            require_publication_target(context, request["args"])
            from cafe.core.execution_checkpoints import (
                load_review_evidence, require_checkpoint, require_verified_review, observe_checkpoint,
            )
            selected = request["args"]["head_oid"]
            readiness = load_review_evidence(issue_dir / "execution_delivery.json")
            current = observe_checkpoint(context, source_revision=selected)
            require_checkpoint(context, readiness.get("checkpoint"), "before_delivery",
                               source_revision=selected, observation=current)
            if context.get("review_policy") == "single_native":
                require_verified_review(context, load_review_evidence(issue_dir / "execution_review.json"),
                                        source_revision=selected, observation=current)
            push_url = request["args"]["push_url"]
            repository = subprocess.run(["gh", "repo", "view", push_url, "--json",
                "nameWithOwner", "--jq", ".nameWithOwner"], cwd=root, capture_output=True,
                text=True, check=True, timeout=20).stdout.strip()
            if len(repository.split("/")) != 2 or any(not part for part in repository.split("/")):
                raise ValueError("authorized repository identity could not be resolved")
            from cafe.core.git_delivery import remote_configuration
            fetch_url = remote_configuration(root, endpoint["remote"], "pr")["fetch"]
            base_repository = repository if fetch_url == push_url else subprocess.run(
                ["gh", "repo", "view", fetch_url, "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
                cwd=root, capture_output=True, text=True, check=True, timeout=20).stdout.strip()
            if not _repository(repository) or not _repository(base_repository):
                raise ValueError("authorized head/base repository identity could not be resolved")
            record.update(head_repository=repository, base_repository=base_repository)
            record["original_attempt"] = {k: v for k, v in record.items() if k != "original_attempt"}
            atomic_write_bytes(path, canonical_json(record))
            before_dispatch()
            require_publication_target(context, request["args"])
        import uuid
        from cafe.core.execution_checkpoints import context_digest
        record = {"delivered": False, "status": "unknown", "authority_digest": context["authority_digest"],
                  "commit": request["args"]["head_oid"], "attempt_id": str(uuid.uuid4()),
                  "workflow_id": context["identity"]["workflow_id"], "execution_identity": context["identity"],
                  "context_digest": context_digest(context), "endpoint": endpoint,
                  "request": evaluation.request.model_dump(mode="json"), "request_fingerprint": evaluation.fingerprint,
                  "approval_task_id": approval_task_id, "correlation_id": correlation_id}
        record["original_attempt"] = dict(record)
        atomic_write_bytes(path, canonical_json(record))
        # No approval bypass: the capability re-evaluates the same host policy.
        before_dispatch()
        if approval_task_id is not None:
            from cafe.core.capability_approvals import CapabilityApprovalService
            service = CapabilityApprovalService(issue_dir=issue_dir,
                workflow_id=context["identity"]["workflow_id"], step="delivery", iteration=1)
            receipt = service.resume(approval_task_id, correlation_id=correlation_id,
                request=evaluation.request, registry=definitions, repo_root=root,
                output_file=output, timeout_sec=240,
                before_dispatch=dispatch_check)
        else:
            run = run_capability_request(repo_root=root, registry=definitions,
                capability_request=request, output_file=output, timeout_sec=240,
                before_dispatch=dispatch_check)
            receipt = run.receipt
        record["receipt"] = receipt
        # A successful external effect is durable before any fallible follow-up
        # read. Keep this exact receipt even when observation is unavailable.
        atomic_write_bytes(path, canonical_json(record))
        if receipt.get("success"):
            expected = expected_pr_binding(context, record, receipt)
            verification = observe_pr_identity(root, expected)
            record["verification"] = verification
            atomic_write_bytes(path, canonical_json(record))
            before_dispatch()
            if verification["status"] == "mismatch":
                raise ValueError("published PR endpoint mismatches original authorization; reconcile read-only")
            if verification["status"] == "verified":
                observed = verification["observation"]
                record.update(delivered=True, status="succeeded", pr={
                    "url": observed["url"], "number": observed["number"],
                    "headRefName": observed["source_branch"], "baseRefName": observed["target_branch"],
                    "headRefOid": observed["head_oid"], "state": observed["state"],
                    "headRepository": {"nameWithOwner": observed["head_repository"]},
                    "baseRepository": {"nameWithOwner": observed["base_repository"]}})
        atomic_write_bytes(path, canonical_json(record))
        return record
    return action()


def _repository(value):
    import re
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value) is not None


def _pr_endpoint(url):
    from urllib.parse import urlparse
    import re
    if not isinstance(url, str): raise ValueError('PR URL is absent')
    parsed = urlparse(url)
    match = re.fullmatch(r'/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)', parsed.path)
    if parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment or match is None:
        raise ValueError('PR URL is not an exact trusted GitHub endpoint')
    return match[1], int(match[2])


def _cli_identity(value):
    if not isinstance(value, dict): return {}
    head = value.get('headRepository') or {}
    base = value.get('baseRepository') or {}
    # A trusted CLI observation's canonical PR URL identifies its base
    # repository and PR number. Missing head identity still requires REST.
    try: url_repository, url_number = _pr_endpoint(value.get('url'))
    except ValueError: url_repository = url_number = None
    return {'url': value.get('url'), 'number': value.get('number', url_number),
            'head_repository': head.get('nameWithOwner') if isinstance(head, dict) else None,
            'base_repository': (base.get('nameWithOwner') if isinstance(base, dict) else None) or url_repository,
            'source_branch': value.get('headRefName'), 'target_branch': value.get('baseRefName'),
            'head_oid': value.get('headRefOid'), 'state': value.get('state')}


def _rest_identity(value):
    if not isinstance(value, dict): return {}
    head, base = value.get('head') or {}, value.get('base') or {}
    if not isinstance(head, dict) or not isinstance(base, dict): return {}
    head_repo, base_repo = head.get('repo') or {}, base.get('repo') or {}
    state = 'MERGED' if value.get('merged') is True else ('OPEN' if value.get('state') == 'open' else 'CLOSED' if value.get('state') == 'closed' else None)
    return {'url': value.get('html_url'), 'number': value.get('number'),
            'head_repository': head_repo.get('full_name') if isinstance(head_repo, dict) else None,
            'base_repository': base_repo.get('full_name') if isinstance(base_repo, dict) else None,
            'source_branch': head.get('ref'), 'target_branch': base.get('ref'), 'head_oid': head.get('sha'), 'state': state}


def verify_pr_identity(expected, cli, rest=None):
    """Fill missing observations only; any observed conflict remains a mismatch."""
    try:
        repository, number = _pr_endpoint(expected.get('url'))
        if repository.casefold() != expected.get('base_repository', '').casefold() or number != expected.get('number'):
            raise ValueError('authorized PR endpoint binding is inconsistent')
        if not all(_repository(expected.get(k)) for k in ['head_repository', 'base_repository']):
            raise ValueError('original repository identities are unavailable')
    except (ValueError, TypeError, AttributeError) as error:
        return {'status': 'unresolved', 'reason': str(error)}
    observed = {}
    for source in [_cli_identity(cli), _rest_identity(rest)]:
        for key, value in source.items():
            if value is None or value == '': continue
            if key == 'number':
                if type(value) is not int or value < 1:
                    return {'status': 'unresolved', 'reason': 'malformed observed PR number'}
            if key != 'number' and not isinstance(value, str):
                return {'status': 'unresolved', 'reason': f'malformed observed {key}'}
            if key.endswith('_repository'):
                if not _repository(value): return {'status': 'unresolved', 'reason': 'malformed repository identity'}
                value = value.casefold()
            if key in observed and observed[key] != value:
                return {'status': 'mismatch', 'reason': f'conflicting {key} observations', 'observation': observed}
            observed[key] = value
            if key == 'state':
                if value not in expected.get('allowed_states', ['OPEN']):
                    return {'status': 'mismatch', 'reason': 'PR state differs from confirmed condition', 'observation': observed}
            elif key in expected:
                wanted = expected[key].casefold() if key.endswith('_repository') else expected[key]
                if value != wanted:
                    return {'status': 'mismatch', 'reason': f'observed {key} differs from original authorization', 'observation': observed}
    required = {'url', 'number', 'head_repository', 'base_repository', 'source_branch', 'target_branch', 'head_oid', 'state'}
    if not required.issubset(observed): return {'status': 'unresolved', 'reason': 'required PR observation is unavailable', 'observation': observed}
    return {'status': 'verified', 'observation': observed}


def observe_pr_identity(root, expected):
    """Authenticated CLI/REST reads for the exact originally authorized PR."""
    import json, subprocess
    cli, rest, errors = {}, None, []
    try:
        repository, number = _pr_endpoint(expected['url'])
        if repository.casefold() != expected['base_repository'].casefold() or number != expected['number']:
            raise ValueError('read endpoint differs from original publication')
    except (ValueError, KeyError, AttributeError) as error:
        return {'status': 'unresolved', 'reason': str(error)}
    def read(argv):
        result = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=True, timeout=20)
        if len(result.stdout.encode()) > 256 * 1024: raise ValueError('PR observation exceeds bounded evidence capacity')
        return json.loads(result.stdout)
    try:
        cli = read(['gh', 'pr', 'view', expected['url'], '--json',
                    'url,number,headRefName,baseRefName,headRefOid,state,headRepository'])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        errors.append({'kind': type(error).__name__, 'returncode': getattr(error, 'returncode', None)})
    initial = verify_pr_identity(expected, cli)
    if initial['status'] == 'mismatch': return initial
    if initial['status'] != 'verified':
        try: rest = read(['gh', 'api', f'repos/{repository}/pulls/{number}'])
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            errors.append({'kind': type(error).__name__, 'returncode': getattr(error, 'returncode', None)})
    result = verify_pr_identity(expected, cli, rest)
    if errors: result['read_diagnostics'] = errors
    return result


def expected_pr_binding(context, record, receipt):
    """A read observation cannot replace original intent or a genuine receipt."""
    inner = receipt.get('execution', receipt)
    outputs = inner.get('outputs', {})
    url = outputs.get('pr_url')
    repository, number = _pr_endpoint(url)
    given_number = outputs.get('pr_number')
    if given_number is not None and str(number) != str(given_number):
        raise ValueError('receipt PR number differs from its URL')
    endpoint = record['endpoint']
    if repository.casefold() != record['base_repository'].casefold():
        raise ValueError('receipt PR endpoint differs from the original base repository')
    if (record.get('authority_digest') != context['authority_digest'] or endpoint != context['delivery_endpoint']
            or record.get('commit') != record['request']['args']['head_oid']):
        raise ValueError('publication intent differs from original execution authority')
    return {'url': url, 'number': number, 'head_repository': record['head_repository'],
        'base_repository': record['base_repository'], 'source_branch': endpoint['source_branch'],
        'target_branch': endpoint['target_branch'], 'head_oid': record['commit'],
        'allowed_states': ['OPEN', 'MERGED'] if 'merge' in endpoint.get('effects', []) else ['OPEN']}
