import 'package:flutter/material.dart';
import 'theme_storage_stub.dart'
    if (dart.library.js_interop) 'theme_storage_web.dart'
    as storage;

final themeMode = ValueNotifier<ThemeMode>(switch (storage.readTheme()) {
  'light' => ThemeMode.light,
  'dark' => ThemeMode.dark,
  _ => ThemeMode.system,
});

void setThemeMode(ThemeMode mode) {
  themeMode.value = mode;
  storage.writeTheme(mode.name);
}

ThemeData dashboardTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final scheme =
      ColorScheme.fromSeed(
        seedColor: const Color(0xff2458e8),
        brightness: brightness,
      ).copyWith(
        surface: dark ? const Color(0xff171c26) : Colors.white,
        primary: dark ? const Color(0xff9bb7ff) : const Color(0xff2458e8),
      );
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: dark
        ? const Color(0xff10141c)
        : const Color(0xfff5f7fb),
    dividerColor: scheme.outlineVariant,
    cardTheme: CardThemeData(
      color: scheme.surface,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: scheme.outlineVariant.withValues(alpha: .6)),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size(48, 44),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size(48, 44),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
      contentPadding: const EdgeInsets.all(16),
    ),
  );
}

class ThemeMenu extends StatelessWidget {
  const ThemeMenu({super.key});
  @override
  Widget build(BuildContext context) => PopupMenuButton<ThemeMode>(
    tooltip: 'Appearance',
    icon: Icon(
      Theme.of(context).brightness == Brightness.dark
          ? Icons.dark_mode_outlined
          : Icons.light_mode_outlined,
    ),
    initialValue: themeMode.value,
    onSelected: setThemeMode,
    itemBuilder: (_) => [
      for (final mode in ThemeMode.values)
        CheckedPopupMenuItem(
          value: mode,
          checked: themeMode.value == mode,
          child: Text(switch (mode) {
            ThemeMode.system => 'Use device theme',
            ThemeMode.light => 'Light mode',
            ThemeMode.dark => 'Dark mode',
          }),
        ),
    ],
  );
}
