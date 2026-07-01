# NewsListResponse


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**items** | [**List[NewsItemOut]**](NewsItemOut.md) |  | 
**total** | **int** |  | 
**offset** | **int** |  | 

## Example

```python
from rugmunch.models.news_list_response import NewsListResponse

# TODO update the JSON string below
json = "{}"
# create an instance of NewsListResponse from a JSON string
news_list_response_instance = NewsListResponse.from_json(json)
# print the JSON string representation of the object
print(NewsListResponse.to_json())

# convert the object into a dict
news_list_response_dict = news_list_response_instance.to_dict()
# create an instance of NewsListResponse from a dict
news_list_response_from_dict = NewsListResponse.from_dict(news_list_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


