# Builtin hooks

Scripts here can be referenced from `config.yaml` with a `builtin:` prefix,
relative to this package, e.g.:

```yaml
hooks:
  pre_archive: builtin:hooks/lutris/prepack.sh
```

Add your own scripts under a subdirectory per launcher (e.g. `lutris/`,
`heroic/`) and reference them the same way.
