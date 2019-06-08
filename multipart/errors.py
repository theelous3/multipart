class MultipartError(Exception):
    ...


class UnexpectedExit(MultipartError):
    ...


class MalformedData(MultipartError):
    ...
