import 'package:juice/juice.dart';

class PermissionOptions extends StatelessWidget {
  const PermissionOptions({
    super.key,
    required this.value,
    required this.onChanged,
    required this.paths,
  });
  final String value;
  final ValueChanged<String> onChanged;
  final TextEditingController paths;
  static List<String> parsePaths(String text) => text
      .split('\n')
      .map((p) => p.trim())
      .where((p) => p.isNotEmpty)
      .toSet()
      .toList();
  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      DropdownButtonFormField<String>(
        initialValue: value,
        isExpanded: true,
        decoration: const InputDecoration(labelText: 'Permissions'),
        items: const [
          DropdownMenuItem(value: 'default', child: Text('CLI defaults')),
          DropdownMenuItem(
            value: 'dashboard_outputs',
            child: Text('Allow dashboard outputs'),
          ),
          DropdownMenuItem(
            value: 'full_autonomy',
            child: Text('Full autonomy'),
          ),
        ],
        onChanged: (v) {
          if (v != null) onChanged(v);
        },
      ),
      const SizedBox(height: 8),
      Text(
        value == 'full_autonomy'
            ? 'Automatically allows all OpenCode tools. This does not create a sandbox. Applies on next launch.'
            : 'Applies on next launch. Output mode allows dashboard reply and artifact folders; other CLI rules still apply.',
      ),
      const SizedBox(height: 12),
      TextFormField(
        controller: paths,
        enabled: value != 'full_autonomy',
        minLines: 2,
        maxLines: 5,
        decoration: const InputDecoration(
          labelText: 'Additional accessible path globs',
          hintText: '~/shared/reference/**\n/home/herdr/projects/**',
          helperText:
              'One pattern per line. Use absolute paths or ~/… on the container.',
          helperMaxLines: 2,
          border: OutlineInputBorder(),
        ),
        validator: (text) {
          final patterns = parsePaths(text ?? '');
          if (patterns.length > 20) return 'Use at most 20 patterns.';
          if (patterns.any(
            (p) =>
                p.length > 500 ||
                p.contains('\u0000') ||
                !(p.startsWith('/') ||
                    p.startsWith('~/') ||
                    p.startsWith(r'$HOME/')),
          )) {
            return 'Use absolute or home-relative patterns under 500 characters.';
          }
          return null;
        },
      ),
      const SizedBox(height: 8),
      Text(
        value == 'full_autonomy'
            ? 'Full autonomy already allows all paths.'
            : 'Allows external-directory access for these patterns. Other read, edit and command rules still apply. Output mode also includes reply and artifact folders automatically.',
      ),
    ],
  );
}
