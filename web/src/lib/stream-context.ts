export function extendWithoutReadingGetters<
  TSource extends object,
  TExtension extends object,
>(source: TSource, extension: TExtension): TSource & TExtension {
  return Object.assign(Object.create(source), extension) as TSource &
    TExtension;
}
