import 'package:flutter/material.dart';

Future<bool> confirmRemoval(
  BuildContext context,
  String name, {
  bool group = false,
}) async =>
    await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Remove $name?'),
        content: Text(
          group
              ? 'Removes this group and closes its facilitator terminal. Member agents remain. History, artifacts and worktree files are preserved.'
              : 'Removes this agent and closes its bound Herdr terminal. History, artifacts and worktree files are preserved. Remove it from any groups first.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Remove'),
          ),
        ],
      ),
    ) ??
    false;
