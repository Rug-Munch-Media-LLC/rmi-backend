# ScanFlag

A risk flag raised by a scan.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**code** | **str** |  | 
**severity** | [**AppDomainTokenModelsRiskLevel**](AppDomainTokenModelsRiskLevel.md) |  | 
**message** | **str** |  | 
**evidence** | **Dict[str, object]** |  | [optional] 

## Example

```python
from rugmunch.models.scan_flag import ScanFlag

# TODO update the JSON string below
json = "{}"
# create an instance of ScanFlag from a JSON string
scan_flag_instance = ScanFlag.from_json(json)
# print the JSON string representation of the object
print(ScanFlag.to_json())

# convert the object into a dict
scan_flag_dict = scan_flag_instance.to_dict()
# create an instance of ScanFlag from a dict
scan_flag_from_dict = ScanFlag.from_dict(scan_flag_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


