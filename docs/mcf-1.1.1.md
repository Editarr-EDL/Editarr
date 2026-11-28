# Movie Content Filter 1.1.1

MCF 1.1.1 is Editarr’s extension to the published MCF 1.1.0 specification. It adds
eight subcategory tokens for timed cues. [Upstream MCF specification](https://github.com/delight-im/MovieContentFilter/blob/master/Website/views/specification.html)

## File format

MCF 1.1.1 files use UTF-8 without a byte order mark and the `.mcf` extension.
The first line is the header below. A blank line follows it.

```text
WEBVTT MovieContentFilter 1.1.1
```

Apply the MCF 1.1.0 rules for metadata, material start and end markers,
millisecond cue times, severity, channels, and comments. In a cue payload, the
first field is a case-sensitive subcategory token. It determines the family.
MCF 1.1.1 accepts every MCF 1.1.0 token and the eight additions below.

## Added subcategories

| Family | Display label | Cue token | Meaning |
| --- | --- | --- | --- |
| Violence | Gore | `gore` | Graphic blood, injury, mutilation, or bodily damage. |
| Violence | Abuse | `abuse` | Abusive treatment, such as physical mistreatment, intimidation, or coercive control. |
| Language | Innuendo | `innuendo` | Indirect sexual references, including suggestive jokes and double entendres. |
| Sex | Rape | `sexualRape` | Rape classified under the sexual content of the cue. |
| Discrimination | Sizeism | `sizeism` | Prejudice or discriminatory treatment based on body size or weight. |
| Discrimination | Ableism | `ableism` | Prejudice or discriminatory treatment based on disability or perceived ability. |
| Discrimination | Ageism | `ageism` | Prejudice or discriminatory treatment based on age. |
| Dispensable scenes | Other | `other` | A scene that can be omitted and has no more specific classification in this family. |

Rape under **Violence** uses the established `rape` token. Rape under **Sex** uses
`sexualRape`. The shared display label lets an author choose the family that best
matches the cue. `adultism` remains available for prejudice against young people.

## Compatibility

- The 1.0.0 and 1.1.0 headers use their existing vocabulary. New subcategory tokens
  require a 1.1.1 header.
- Editarr imports all three versions and records the original file's version.
- Editarr exports 1.1.1 when an exported cue uses any of the eight additions. It
  exports 1.1.0 when all exported cues use the earlier vocabulary.
- VideoSkip `gor` and `oth` labels enter new Editarr imports as `gore` and `other`,
  respectively. Existing saved classifications retain their original values.
- Update the community service and connected Editarr clients before sharing cues
  with the new tokens. Older clients can reject a community download that contains
  one of these tokens.

## Example

```text
WEBVTT MovieContentFilter 1.1.1

NOTE
TITLE Example Film
TYPE movie

NOTE
START 00:00:00.000
END 01:30:00.000

00:10:05.000 --> 00:10:08.500
innuendo=medium=audio

00:32:15.250 --> 00:32:19.000
gore=high=video
```
