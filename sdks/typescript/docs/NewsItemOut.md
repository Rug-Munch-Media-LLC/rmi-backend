
# NewsItemOut


## Properties

Name | Type
------------ | -------------
`newsId` | string
`url` | string
`title` | string
`summary` | string
`source` | string
`publishedAt` | Date
`chainsMentioned` | Array&lt;string&gt;
`tokensMentioned` | Array&lt;string&gt;
`sentimentScore` | number
`score` | number

## Example

```typescript
import type { NewsItemOut } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "newsId": null,
  "url": null,
  "title": null,
  "summary": null,
  "source": null,
  "publishedAt": null,
  "chainsMentioned": null,
  "tokensMentioned": null,
  "sentimentScore": null,
  "score": null,
} satisfies NewsItemOut

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as NewsItemOut
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


