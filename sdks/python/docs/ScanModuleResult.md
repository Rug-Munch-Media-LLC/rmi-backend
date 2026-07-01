# ScanModuleResult

A single scanner module's outcome.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**module** | **str** |  | 
**status** | **str** |  | 
**error** | **str** |  | [optional] 
**data** | **Dict[str, object]** |  | [optional] 

## Example

```python
from rugmunch.models.scan_module_result import ScanModuleResult

# TODO update the JSON string below
json = "{}"
# create an instance of ScanModuleResult from a JSON string
scan_module_result_instance = ScanModuleResult.from_json(json)
# print the JSON string representation of the object
print(ScanModuleResult.to_json())

# convert the object into a dict
scan_module_result_dict = scan_module_result_instance.to_dict()
# create an instance of ScanModuleResult from a dict
scan_module_result_from_dict = ScanModuleResult.from_dict(scan_module_result_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


