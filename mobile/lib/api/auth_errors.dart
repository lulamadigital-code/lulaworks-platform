import 'dart:async';
import 'dart:io';

import 'api_client.dart';

/// Central place that turns any thrown error into ONE clean, user-facing line.
/// Screens call this instead of printing raw API JSON or a blanket
/// "Something went wrong". Keeping it in one function means every auth surface
/// (login, forgot password, session expiry) speaks the same language.
///
/// [context] lets a caller tune the wording — on the LOGIN form a 400/401 means
/// "wrong email or password"; elsewhere a 401 means the session expired.
enum AuthErrorContext { login, resetRequest, general }

String authErrorMessage(Object error, {AuthErrorContext context = AuthErrorContext.general}) {
  // Known HTTP failures from the API.
  if (error is ApiException) {
    final code = error.statusCode;
    switch (code) {
      case 400:
      case 401:
        if (context == AuthErrorContext.login) {
          return 'Email or password is incorrect.';
        }
        if (context == AuthErrorContext.general) {
          return 'Your session has expired. Please sign in again.';
        }
        return "We couldn't process that request. Please check the details and try again.";
      case 403:
        return "Your account doesn't have access to this. Contact your administrator.";
      case 404:
        return "We couldn't find what you were looking for.";
      case 408:
        return 'The request timed out. Please try again.';
      case 422:
        return 'Please check the details you entered and try again.';
      case 429:
        return 'Too many attempts. Please wait a moment and try again.';
    }
    if (code >= 500) {
      return 'Lulaworks is temporarily unavailable. Please try again shortly.';
    }
    // A classified-but-uncommon status — the server's own message is safe to show
    // (it is already scrubbed by the backend error envelope).
    return error.message;
  }

  // Connectivity / transport failures.
  if (error is SocketException || error is HttpException) {
    return 'Unable to connect to Lulaworks. Check your internet connection and try again.';
  }
  if (error is TimeoutException) {
    return 'The request timed out. Check your connection and try again.';
  }
  final s = error.toString();
  if (s.contains('SocketException') ||
      s.contains('Failed host lookup') ||
      s.contains('Connection') ||
      s.contains('Network is unreachable')) {
    return 'Unable to connect to Lulaworks. Check your internet connection and try again.';
  }
  if (s.contains('TimeoutException')) {
    return 'The request timed out. Check your connection and try again.';
  }

  // Genuinely unclassifiable — the only case where a generic line is honest.
  return 'Something went wrong. Please try again.';
}
