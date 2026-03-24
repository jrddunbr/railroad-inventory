# Permissions

Permissions are stored as a list of objects with this shape:

```json
[
  {"scope": "inventory/*", "access": "READ"},
  {"scope": "admin/users", "access": "WRITE"}
]
```

The text form accepted by the admin UI is one permission per line:

```text
inventory/* READ
admin/users WRITE
```

## Access Levels

- `READ`: View pages and data for the matching scope.
- `WRITE`: Create, edit, delete, or otherwise perform modifying actions for the matching scope.

## Scope Matching

- Scopes are hierarchical and use `/` separators.
- `*` matches all descendants from that point downward.
- `* WRITE` grants full access across the platform.
- `inventory/* READ` grants read access to all inventory-related scopes.
- `inventory/cars WRITE` grants write access to the cars scope only.
- `inventory/cars/c38 WRITE` is syntactically valid and represents a deeper leaf under `inventory/cars`.

## Leaf Scopes In Use

These are the concrete scopes currently used by route gating and navigation in the application.

### Administration

- `admin`
- `admin/users`
- `admin/settings`

### Analysis

- `search`
- `search/api`
- `reports`

### Inventory

- `inventory`
- `railroads`
- `inventory/locations`
- `inventory/car-classes`
- `inventory/locomotive-classes`
- `inventory/cars`
- `inventory/consists`
- `inventory/loads`
- `inventory/loads/placements`
- `inventory/tools`
- `inventory/parts`

### Tools

- `tools`
- `tools/consist-creation`
- `tools/aar-plate-viewer`
- `tools/prr-home-shop-repair`

## Common Examples

### Read-only inventory user

```text
inventory/* READ
railroads READ
search READ
reports READ
```

### Car manager

```text
inventory/cars WRITE
inventory/car-classes READ
inventory/locations READ
railroads READ
```

### Operations user

```text
inventory/cars READ
inventory/consists WRITE
inventory/loads WRITE
inventory/loads/placements WRITE
search READ
reports READ
```

### Tool room user

```text
inventory/tools WRITE
inventory/parts WRITE
inventory/locations READ
```

### User administrator

```text
admin/users WRITE
```

### Full administrator

```text
* WRITE
```

## Notes

- Users without configured MFA are effectively read-only even if they have stored `WRITE` permissions.
- Self-service account pages are handled separately from the normal scope checks so users can manage their own account security.
