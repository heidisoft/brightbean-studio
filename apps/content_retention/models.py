# No models of its own: the policy lives in WorkspaceSetting rows and the
# per-post state on composer.Post. This module exists because Django only sends
# post_migrate (which registers the recurring task) to apps with a models module.
