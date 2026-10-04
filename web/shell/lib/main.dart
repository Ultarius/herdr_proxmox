import 'package:jaspr/client.dart';

void main() {
  runApp(
    Component.element(
      tag: 'main',
      children: [
        Component.element(
          tag: 'iframe',
          attributes: {
            'src': '/dashboard/',
            'title': 'Herdr workspace and agent dashboard',
          },
        ),
      ],
    ),
  );
}
